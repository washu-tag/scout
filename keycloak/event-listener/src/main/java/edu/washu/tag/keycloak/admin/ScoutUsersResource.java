package edu.washu.tag.keycloak.admin;

import jakarta.ws.rs.BadRequestException;
import jakarta.ws.rs.ClientErrorException;
import jakarta.ws.rs.Consumes;
import jakarta.ws.rs.DELETE;
import jakarta.ws.rs.ForbiddenException;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.NotAuthorizedException;
import jakarta.ws.rs.NotFoundException;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.PathParam;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.QueryParam;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.regex.Pattern;
import org.jboss.logging.Logger;
import org.keycloak.events.admin.OperationType;
import org.keycloak.events.admin.ResourceType;
import org.keycloak.models.GroupModel;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserModel;
import org.keycloak.representations.idm.GroupRepresentation;
import org.keycloak.representations.userprofile.config.UPAttribute;
import org.keycloak.representations.userprofile.config.UPConfig;
import org.keycloak.services.managers.AppAuthManager;
import org.keycloak.services.managers.AuthenticationManager.AuthResult;
import org.keycloak.services.resources.admin.AdminAuth;
import org.keycloak.services.resources.admin.AdminEventBuilder;
import org.keycloak.userprofile.UserProfileProvider;

/**
 * JAX-RS resource backing the Scout user-administration console. All endpoints
 * require a bearer token whose user is in {@code scout-admin}; the resource then
 * acts as that admin (no standing admin-API credential).
 *
 * <p>The data-access attributes are <b>not hardcoded</b> — they're discovered
 * from the realm User Profile (attributes annotated {@code scoutAuthz=true}),
 * itself rendered from {@code trino_attribute_filters}. So {@link #buildSchema()}
 * drives the UI form and {@link #applyApproval}/{@link #setAttributes} validate
 * against whatever dimensions are configured: add a dimension in inventory and it
 * flows through with no change here.
 *
 * <p>Beyond approval the console edits a user's attributes, promotes/demotes
 * {@code scout-admin}, and offboards (removes all Scout group membership) — each
 * guarded server-side ({@link #demote}/{@link #offboard} reject removing the last
 * admin, {@link #offboard} blocks self-offboard) regardless of the UI. Every
 * mutation fires a {@code GROUP_MEMBERSHIP}/{@code USER} admin event (see
 * {@link #fireGroupMembershipEvent}) so it propagates to OPA, drives the
 * approval/offboard email, and lands in the admin-events audit log.
 *
 * <p>Structure: the {@code @GET}/{@code @POST}/{@code @DELETE} methods are thin
 * adapters that authenticate, mutate, emit the event, and translate domain
 * exceptions to HTTP status; the core logic is plain and unit-tested directly.
 */
public class ScoutUsersResource {

    private static final Logger log = Logger.getLogger(ScoutUsersResource.class);

    static final String SCOUT_USER_GROUP = "scout-user";
    static final String SCOUT_ADMIN_GROUP = "scout-admin";
    static final String AUTHZ_ANNOTATION = "scoutAuthz";
    private static final String TERMS_ACCEPTED_ATTR = "scout_terms_accepted_at";
    private static final String APPROVAL_EMAIL_ATTR = "scout_admin_approval_email_sent_at";

    private final KeycloakSession session;

    ScoutUsersResource(KeycloakSession session) {
        this.session = session;
    }

    // --- DTOs (serialized by Keycloak's Jackson) ---------------------------

    /** A data-access attribute the UI should render (discovered from the profile). */
    public record AttrSchema(String name, String displayName, boolean multivalued,
                             String inputType, List<String> options, String defaultValue) {
    }

    /** A user who accepted the Terms but isn't yet approved into scout-user. */
    public record PendingUser(String id, String username, String email, String name,
                              String requestedAt) {
    }

    /** A user row for the admin console; {@code attributes} are filtered to scoutAuthz keys only. */
    public record ScoutUser(String id, String username, String email, String name,
                            String status, boolean isAdmin, Map<String, List<String>> attributes) {
    }

    /** Approval request body: the user and the data-access attribute values to grant. */
    public record ApproveRequest(String userId, Map<String, List<String>> attributes) {
    }

    /** Body for editing an existing user's data-access attributes. */
    public record AttributesRequest(Map<String, List<String>> attributes) {
    }

    /** What an offboard removed, so the adapter can emit the matching events. */
    record OffboardResult(String username, boolean wasAdmin) {
    }

    // --- Endpoints (thin JAX-RS adapters) ----------------------------------

    /** {@return the dynamic data-access attribute schema the UI renders} scout-admin only. */
    @GET
    @Path("schema")
    @Produces(MediaType.APPLICATION_JSON)
    public List<AttrSchema> schema() {
        requireScoutAdmin();
        return buildSchema();
    }

    /** {@return users awaiting approval} scout-admin only. */
    @GET
    @Path("pending")
    @Produces(MediaType.APPLICATION_JSON)
    public List<PendingUser> pending() {
        requireScoutAdmin();
        return findPending();
    }

    /** {@return Scout users filtered by status (pending/active/admin) and optional search} scout-admin only. */
    @GET
    @Path("users")
    @Produces(MediaType.APPLICATION_JSON)
    public List<ScoutUser> users(@QueryParam("status") String status, @QueryParam("search") String search) {
        requireScoutAdmin();
        return listUsers(status, search);
    }

    /** Approve a user: set the submitted data-access attributes and join scout-user (scout-admin only). */
    @POST
    @Path("approve")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.APPLICATION_JSON)
    public Response approve(ApproveRequest req) {
        AuthResult auth = requireScoutAdmin();
        String username;
        try {
            username = applyApproval(req);
        } catch (IllegalArgumentException e) {
            throw new BadRequestException(e.getMessage());
        } catch (NoSuchElementException e) {
            throw new NotFoundException(e.getMessage());
        }
        fireGroupMembershipEvent(auth, req.userId(), SCOUT_USER_GROUP, OperationType.CREATE);
        return ok("approved", username);
    }

    /** Edit an approved user's data-access attributes (scout-admin only). */
    @POST
    @Path("users/{id}/attributes")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.APPLICATION_JSON)
    public Response setUserAttributes(@PathParam("id") String id, AttributesRequest req) {
        AuthResult auth = requireScoutAdmin();
        String username;
        try {
            username = setAttributes(id, req == null ? null : req.attributes());
        } catch (IllegalArgumentException e) {
            throw new BadRequestException(e.getMessage());
        } catch (NoSuchElementException e) {
            throw new NotFoundException(e.getMessage());
        }
        fireUserUpdatedEvent(auth, id);
        return ok("updated", username);
    }

    /** Promote a user to scout-admin (scout-admin only). */
    @POST
    @Path("users/{id}/admin")
    @Produces(MediaType.APPLICATION_JSON)
    public Response promoteAdmin(@PathParam("id") String id) {
        AuthResult auth = requireScoutAdmin();
        String username;
        try {
            username = promote(id);
        } catch (NoSuchElementException e) {
            throw new NotFoundException(e.getMessage());
        }
        fireGroupMembershipEvent(auth, id, SCOUT_ADMIN_GROUP, OperationType.CREATE);
        return ok("promoted", username);
    }

    /** Demote a user from scout-admin; rejected with 409 if they are the last admin (scout-admin only). */
    @DELETE
    @Path("users/{id}/admin")
    @Produces(MediaType.APPLICATION_JSON)
    public Response demoteAdmin(@PathParam("id") String id) {
        AuthResult auth = requireScoutAdmin();
        String username;
        try {
            username = demote(id);
        } catch (NoSuchElementException e) {
            throw new NotFoundException(e.getMessage());
        } catch (IllegalStateException e) {
            throw new ClientErrorException(e.getMessage(), Response.Status.CONFLICT);
        }
        fireGroupMembershipEvent(auth, id, SCOUT_ADMIN_GROUP, OperationType.DELETE);
        return ok("demoted", username);
    }

    /** Offboard a user: remove all Scout group membership. Blocks self-offboard and last-admin (409). scout-admin only. */
    @DELETE
    @Path("users/{id}/membership")
    @Produces(MediaType.APPLICATION_JSON)
    public Response offboardUser(@PathParam("id") String id) {
        AuthResult auth = requireScoutAdmin();
        OffboardResult result;
        try {
            result = offboard(auth.user().getId(), id);
        } catch (NoSuchElementException e) {
            throw new NotFoundException(e.getMessage());
        } catch (IllegalStateException e) {
            throw new ClientErrorException(e.getMessage(), Response.Status.CONFLICT);
        }
        fireGroupMembershipEvent(auth, id, SCOUT_USER_GROUP, OperationType.DELETE);
        if (result.wasAdmin()) {
            fireGroupMembershipEvent(auth, id, SCOUT_ADMIN_GROUP, OperationType.DELETE);
        }
        return ok("offboarded", result.username());
    }

    AuthResult requireScoutAdmin() {
        AuthResult auth = new AppAuthManager.BearerTokenAuthenticator(session).authenticate();
        if (auth == null || auth.user() == null) {
            throw new NotAuthorizedException("Bearer");
        }
        if (!isScoutAdmin(auth.user())) {
            throw new ForbiddenException("requires membership in " + SCOUT_ADMIN_GROUP);
        }
        return auth;
    }

    /**
     * Emit a {@code GROUP_MEMBERSHIP} admin event for a user's group change.
     * The SPI mutates the user model directly (no admin REST call), so without
     * this the change is invisible to the realm's admin-event listeners: the OPA
     * bundle publisher re-snapshots the user on this event (so the grant actually
     * reaches Trino), the approval/offboard email listener keys off
     * {@code scout-user} in the representation, and the action lands in the
     * admin-events audit log. The {@code users/{id}/...} resourcePath is what the
     * publisher's user-id extractor reads.
     */
    private void fireGroupMembershipEvent(AuthResult auth, String userId, String groupName, OperationType op) {
        RealmModel realm = session.getContext().getRealm();
        GroupModel group = topLevelGroup(realm, groupName);
        String groupId = group != null ? group.getId() : groupName;
        GroupRepresentation rep = new GroupRepresentation();
        rep.setName(groupName);
        rep.setId(group != null ? group.getId() : null);
        adminEvent(auth, realm)
                .operation(op)
                .resource(ResourceType.GROUP_MEMBERSHIP)
                .resourcePath("users", userId, "groups", groupId)
                .representation(rep)
                .success();
    }

    /** Emit a {@code USER}/{@code UPDATE} admin event so an attribute-only change re-snapshots to OPA + audits. */
    private void fireUserUpdatedEvent(AuthResult auth, String userId) {
        RealmModel realm = session.getContext().getRealm();
        adminEvent(auth, realm)
                .operation(OperationType.UPDATE)
                .resource(ResourceType.USER)
                .resourcePath("users", userId)
                .success();
    }

    private AdminEventBuilder adminEvent(AuthResult auth, RealmModel realm) {
        AdminAuth adminAuth = new AdminAuth(realm, auth.token(), auth.user(), auth.client());
        return new AdminEventBuilder(realm, adminAuth, session, session.getContext().getConnection());
    }

    private Response ok(String status, String username) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", status);
        body.put("username", username);
        return Response.ok(body).build();
    }

    // --- Core logic (package-private, unit-tested) -------------------------

    boolean isScoutAdmin(UserModel user) {
        return user.getGroupsStream().anyMatch(g -> SCOUT_ADMIN_GROUP.equals(g.getName()));
    }

    List<AttrSchema> buildSchema() {
        return authzAttributes().values().stream()
                .map(a -> new AttrSchema(
                        a.getName(),
                        a.getDisplayName(),
                        a.isMultivalued(),
                        annotation(a, "inputType"),
                        options(a),
                        annotation(a, "scoutDefault")))
                .toList();
    }

    List<PendingUser> findPending() {
        RealmModel realm = session.getContext().getRealm();
        // Scout realms are small; a full scan is fine and keeps the query simple.
        return session.users().searchForUserStream(realm, Map.<String, String>of())
                .filter(u -> u.getFirstAttribute(TERMS_ACCEPTED_ATTR) != null)
                .filter(u -> u.getGroupsStream().noneMatch(g -> SCOUT_USER_GROUP.equals(g.getName())))
                .map(u -> new PendingUser(u.getId(), u.getUsername(), u.getEmail(),
                        fullName(u), u.getFirstAttribute(APPROVAL_EMAIL_ATTR)))
                .toList();
    }

    /**
     * Validate the submitted attributes against the dynamic schema, then set them
     * and join scout-user. Throws {@link IllegalArgumentException} (bad request)
     * or {@link NoSuchElementException} (not found) — translated to HTTP by the
     * {@link #approve} adapter. Validates everything before writing anything, so
     * a bad request never leaves a half-applied grant.
     */
    String applyApproval(ApproveRequest req) {
        RealmModel realm = session.getContext().getRealm();
        UserModel user = requireUser(realm, req == null ? null : req.userId());
        Map<String, List<String>> attributes = validateAttributes(req.attributes());
        GroupModel scoutUser = requireGroup(realm, SCOUT_USER_GROUP);
        attributes.forEach(user::setAttribute);
        user.joinGroup(scoutUser);
        log.infof("scout-users: approved %s (attributes set: %s)",
                user.getUsername(), attributes.keySet());
        return user.getUsername();
    }

    /** Enforce the attribute's own constraints (options / pattern / cardinality). */
    void validate(UPAttribute attr, List<String> values) {
        if (!attr.isMultivalued() && values.size() > 1) {
            throw new IllegalArgumentException(attr.getName() + " is single-valued");
        }
        List<String> opts = options(attr);
        if (!opts.isEmpty()) {
            for (String v : values) {
                if (!opts.contains(v)) {
                    throw new IllegalArgumentException(attr.getName() + ": '" + v + "' is not an allowed value");
                }
            }
            return;
        }
        String pattern = patternOf(attr);
        if (pattern != null) {
            Pattern compiled = Pattern.compile(pattern);
            for (String v : values) {
                if (!compiled.matcher(v).matches()) {
                    throw new IllegalArgumentException(attr.getName() + ": '" + v + "' does not match the allowed format");
                }
            }
        }
    }

    /**
     * Validate submitted attribute values against the dynamic schema and reject
     * any key that isn't a {@code scoutAuthz} attribute. Validates everything
     * before the caller writes anything. {@return the map to write}.
     */
    Map<String, List<String>> validateAttributes(Map<String, List<String>> attributes) {
        Map<String, UPAttribute> allowed = authzAttributes();
        Map<String, List<String>> attrs = attributes == null ? Map.of() : attributes;
        for (Map.Entry<String, List<String>> e : attrs.entrySet()) {
            UPAttribute attr = allowed.get(e.getKey());
            if (attr == null) {
                throw new IllegalArgumentException("not a Scout data-access attribute: " + e.getKey());
            }
            validate(attr, e.getValue() == null ? List.of() : e.getValue());
        }
        return attrs;
    }

    /** {@return Scout users matching the status filter (pending/active/admin) and optional search}. */
    List<ScoutUser> listUsers(String status, String search) {
        RealmModel realm = session.getContext().getRealm();
        Map<String, String> params = (search == null || search.isBlank())
                ? Map.of() : Map.of(UserModel.SEARCH, search);
        return session.users().searchForUserStream(realm, params)
                .map(this::toScoutUser)
                .filter(u -> statusMatches(u, status))
                .toList();
    }

    /** Project a user to the console view: derived status, admin flag, and the scoutAuthz attributes only. */
    ScoutUser toScoutUser(UserModel u) {
        List<String> groups = u.getGroupsStream().map(GroupModel::getName).toList();
        boolean admin = groups.contains(SCOUT_ADMIN_GROUP);
        boolean active = admin || groups.contains(SCOUT_USER_GROUP);
        boolean termsAccepted = u.getFirstAttribute(TERMS_ACCEPTED_ATTR) != null;
        String status = admin ? "admin" : active ? "active" : termsAccepted ? "pending" : "none";

        Map<String, List<String>> all = u.getAttributes();
        Map<String, List<String>> attrs = new LinkedHashMap<>();
        for (String key : authzAttributes().keySet()) {
            List<String> values = all == null ? null : all.get(key);
            if (values != null && !values.isEmpty()) {
                attrs.put(key, values);
            }
        }
        return new ScoutUser(u.getId(), u.getUsername(), u.getEmail(), fullName(u), status, admin, attrs);
    }

    /** Whether a user matches the table's status filter. "active" includes admins; blank/"all" matches everything. */
    boolean statusMatches(ScoutUser u, String filter) {
        if (filter == null || filter.isBlank() || "all".equalsIgnoreCase(filter)) {
            return true;
        }
        return switch (filter.toLowerCase()) {
            case "pending" -> "pending".equals(u.status());
            case "active" -> "active".equals(u.status()) || "admin".equals(u.status());
            case "admin", "admins" -> u.isAdmin();
            default -> false;
        };
    }

    /** Set an approved user's data-access attributes (validated). No group change. */
    String setAttributes(String userId, Map<String, List<String>> attributes) {
        RealmModel realm = session.getContext().getRealm();
        UserModel user = requireUser(realm, userId);
        Map<String, List<String>> attrs = validateAttributes(attributes);
        attrs.forEach(user::setAttribute);
        log.infof("scout-users: set attributes %s on %s", attrs.keySet(), user.getUsername());
        return user.getUsername();
    }

    /** Promote a user to scout-admin (idempotent). */
    String promote(String userId) {
        RealmModel realm = session.getContext().getRealm();
        UserModel user = requireUser(realm, userId);
        user.joinGroup(requireGroup(realm, SCOUT_ADMIN_GROUP));
        log.infof("scout-users: promoted %s to %s", user.getUsername(), SCOUT_ADMIN_GROUP);
        return user.getUsername();
    }

    /** Demote a user from scout-admin. Self-demote is allowed; removing the last admin throws IllegalStateException. */
    String demote(String userId) {
        RealmModel realm = session.getContext().getRealm();
        UserModel user = requireUser(realm, userId);
        GroupModel admin = requireGroup(realm, SCOUT_ADMIN_GROUP);
        if (isScoutAdmin(user) && countScoutAdmins(realm) <= 1) {
            throw new IllegalStateException("cannot remove the last " + SCOUT_ADMIN_GROUP);
        }
        user.leaveGroup(admin);
        log.infof("scout-users: demoted %s from %s", user.getUsername(), SCOUT_ADMIN_GROUP);
        return user.getUsername();
    }

    /**
     * Offboard a user: remove scout-user and (if present) scout-admin. Blocks
     * offboarding yourself and removing the last admin (both IllegalStateException).
     * {@return what was removed, so the adapter can emit the matching events}.
     */
    OffboardResult offboard(String actorId, String userId) {
        if (actorId != null && actorId.equals(userId)) {
            throw new IllegalStateException("cannot offboard yourself");
        }
        RealmModel realm = session.getContext().getRealm();
        UserModel user = requireUser(realm, userId);
        boolean wasAdmin = isScoutAdmin(user);
        if (wasAdmin && countScoutAdmins(realm) <= 1) {
            throw new IllegalStateException(
                    "cannot remove the last " + SCOUT_ADMIN_GROUP + "; promote another admin first");
        }
        GroupModel scoutUser = topLevelGroup(realm, SCOUT_USER_GROUP);
        if (scoutUser != null) {
            user.leaveGroup(scoutUser);
        }
        if (wasAdmin) {
            GroupModel scoutAdmin = topLevelGroup(realm, SCOUT_ADMIN_GROUP);
            if (scoutAdmin != null) {
                user.leaveGroup(scoutAdmin);
            }
        }
        log.infof("scout-users: offboarded %s (wasAdmin=%s)", user.getUsername(), wasAdmin);
        return new OffboardResult(user.getUsername(), wasAdmin);
    }

    /** {@return the number of scout-admins, capped at 2 — enough to detect the last-admin case}. */
    long countScoutAdmins(RealmModel realm) {
        GroupModel admin = topLevelGroup(realm, SCOUT_ADMIN_GROUP);
        if (admin == null) {
            return 0;
        }
        return session.users().getGroupMembersStream(realm, admin, 0, 2).count();
    }

    private UserModel requireUser(RealmModel realm, String userId) {
        if (userId == null || userId.isBlank()) {
            throw new IllegalArgumentException("userId is required");
        }
        UserModel user = session.users().getUserById(realm, userId);
        if (user == null) {
            throw new NoSuchElementException("user not found: " + userId);
        }
        return user;
    }

    private GroupModel requireGroup(RealmModel realm, String name) {
        GroupModel group = topLevelGroup(realm, name);
        if (group == null) {
            throw new NoSuchElementException("group not found: " + name);
        }
        return group;
    }

    // --- helpers ------------------------------------------------------------

    /** Name -> definition for every User Profile attribute annotated scoutAuthz=true. */
    Map<String, UPAttribute> authzAttributes() {
        UPConfig config = session.getProvider(UserProfileProvider.class).getConfiguration();
        Map<String, UPAttribute> out = new LinkedHashMap<>();
        for (UPAttribute a : config.getAttributes()) {
            if ("true".equals(annotation(a, AUTHZ_ANNOTATION))) {
                out.put(a.getName(), a);
            }
        }
        return out;
    }

    @SuppressWarnings("unchecked")
    private List<String> options(UPAttribute attr) {
        Map<String, Object> opt = validation(attr, "options");
        Object values = opt == null ? null : opt.get("options");
        return values instanceof List ? (List<String>) values : List.of();
    }

    private String patternOf(UPAttribute attr) {
        Map<String, Object> p = validation(attr, "pattern");
        Object pattern = p == null ? null : p.get("pattern");
        return pattern == null ? null : pattern.toString();
    }

    private Map<String, Object> validation(UPAttribute attr, String key) {
        Map<String, Map<String, Object>> validations = attr.getValidations();
        return validations == null ? null : validations.get(key);
    }

    private String annotation(UPAttribute attr, String key) {
        Map<String, Object> annotations = attr.getAnnotations();
        Object value = annotations == null ? null : annotations.get(key);
        return value == null ? null : value.toString();
    }

    private String fullName(UserModel u) {
        String first = u.getFirstName();
        String last = u.getLastName();
        if (first == null && last == null) {
            return u.getUsername();
        }
        return ((first == null ? "" : first) + " " + (last == null ? "" : last)).trim();
    }

    private GroupModel topLevelGroup(RealmModel realm, String name) {
        return realm.getGroupsStream()
                .filter(g -> name.equals(g.getName()))
                .findFirst()
                .orElse(null);
    }
}
