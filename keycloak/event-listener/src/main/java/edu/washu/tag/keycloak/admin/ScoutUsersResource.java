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
import java.util.Set;
import java.util.regex.Pattern;
import org.jboss.logging.Logger;
import org.keycloak.events.admin.OperationType;
import org.keycloak.events.admin.ResourceType;
import org.keycloak.models.GroupModel;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.RoleModel;
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
 * JAX-RS resource backing the Scout user-administration console. Endpoints are
 * capability-gated (ADR 0026): most require the {@code manage-users} realm role
 * — held by {@code scout-user-manager} members and, via the realm template's
 * group mapping, by {@code scout-admin} — while promoting/demoting
 * {@code scout-admin} itself requires scout-admin membership. The resource acts
 * as the authenticated caller (no standing admin-API credential).
 *
 * <p>The data-access attributes are <b>not hardcoded</b> — they're discovered
 * from the realm User Profile (attributes annotated {@code scoutAuthz=true}),
 * itself rendered from {@code trino_attribute_filters}. So {@link #buildSchema()}
 * drives the UI form and {@link #applyApproval}/{@link #setAttributes} validate
 * against whatever dimensions are configured: add a dimension in inventory and it
 * flows through with no change here.
 *
 * <p>Beyond approval the console edits a user's attributes, grants/revokes the
 * delegated {@code scout-user-manager} role, promotes/demotes {@code scout-admin},
 * and offboards (removes all Scout group membership) — each guarded server-side
 * regardless of the UI: a target in scout-admin can only be modified by a
 * scout-admin ({@link #requireCanModifyTarget}), user-manager is only grantable
 * to approved scout-users, and {@link #offboard} blocks self-offboard. Every
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
    static final String SCOUT_USER_MANAGER_GROUP = "scout-user-manager";
    // Capability realm role (ADR 0026): one name gates the console for both
    // scout-admin and scout-user-manager — the realm template maps the role to
    // both groups, so there is no "or admin" logic anywhere in this resource.
    static final String MANAGE_USERS_ROLE = "manage-users";
    static final String AUTHZ_ANNOTATION = "scoutAuthz";
    // Defense in depth: only accept tokens audienced for this API. The launchpad
    // client adds aud=scout-users-api via an audience mapper (realm template), so a
    // scout-admin's token minted for another resource (e.g. aud=trino) can't reach
    // an API that grants scout-admin -> realm-admin. Audiences the SPI; the -api
    // suffix keeps it distinct from the scout-user group. A fixed contract string
    // like the group names above — both sides live in this repo.
    static final String REQUIRED_AUDIENCE = "scout-users-api";
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
                            String status, boolean isAdmin, boolean isUserManager,
                            Map<String, List<String>> attributes) {
    }

    /** Approval request body: the user and the data-access attribute values to grant. */
    public record ApproveRequest(String userId, Map<String, List<String>> attributes) {
    }

    /** Body for editing an existing user's data-access attributes. */
    public record AttributesRequest(Map<String, List<String>> attributes) {
    }

    /** What an offboard removed, so the adapter can emit the matching events. */
    record OffboardResult(String username, boolean wasAdmin, boolean wasManager) {
    }

    // --- Endpoints (thin JAX-RS adapters) ----------------------------------

    /** {@return the dynamic data-access attribute schema the UI renders} manage-users capability. */
    @GET
    @Path("schema")
    @Produces(MediaType.APPLICATION_JSON)
    public List<AttrSchema> schema() {
        requireCapability(MANAGE_USERS_ROLE);
        return buildSchema();
    }

    /** {@return users awaiting approval} manage-users capability. */
    @GET
    @Path("pending")
    @Produces(MediaType.APPLICATION_JSON)
    public List<PendingUser> pending() {
        requireCapability(MANAGE_USERS_ROLE);
        return findPending();
    }

    /** {@return Scout users filtered by status (pending/active/admin) and optional search} manage-users capability. */
    @GET
    @Path("users")
    @Produces(MediaType.APPLICATION_JSON)
    public List<ScoutUser> users(@QueryParam("status") String status, @QueryParam("search") String search) {
        requireCapability(MANAGE_USERS_ROLE);
        return listUsers(status, search);
    }

    /** Approve a user: set the submitted data-access attributes and join scout-user (manage-users capability). */
    @POST
    @Path("approve")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.APPLICATION_JSON)
    public Response approve(ApproveRequest req) {
        AuthResult auth = requireCapability(MANAGE_USERS_ROLE);
        String username;
        try {
            username = applyApproval(auth.user(), req);
        } catch (IllegalArgumentException e) {
            throw new BadRequestException(e.getMessage());
        } catch (NoSuchElementException e) {
            throw new NotFoundException(e.getMessage());
        } catch (IllegalStateException e) {
            throw new ClientErrorException(e.getMessage(), Response.Status.CONFLICT);
        } catch (SecurityException e) {
            throw new ForbiddenException(e.getMessage());
        }
        fireGroupMembershipEvent(auth, req.userId(), username, SCOUT_USER_GROUP, OperationType.CREATE);
        return ok("approved", username);
    }

    /** Edit an approved user's data-access attributes (manage-users capability). */
    @POST
    @Path("users/{id}/attributes")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.APPLICATION_JSON)
    public Response setUserAttributes(@PathParam("id") String id, AttributesRequest req) {
        AuthResult auth = requireCapability(MANAGE_USERS_ROLE);
        String username;
        try {
            username = setAttributes(auth.user(), id, req == null ? null : req.attributes());
        } catch (IllegalArgumentException e) {
            throw new BadRequestException(e.getMessage());
        } catch (NoSuchElementException e) {
            throw new NotFoundException(e.getMessage());
        } catch (SecurityException e) {
            throw new ForbiddenException(e.getMessage());
        }
        fireUserUpdatedEvent(auth, id, username);
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
        fireGroupMembershipEvent(auth, id, username, SCOUT_ADMIN_GROUP, OperationType.CREATE);
        return ok("promoted", username);
    }

    /** Demote a user from scout-admin (scout-admin only). */
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
        }
        fireGroupMembershipEvent(auth, id, username, SCOUT_ADMIN_GROUP, OperationType.DELETE);
        return ok("demoted", username);
    }

    /** Grant the delegated user-manager role (manage-users capability; an admin target requires an admin caller). */
    @POST
    @Path("users/{id}/manager")
    @Produces(MediaType.APPLICATION_JSON)
    public Response promoteUserManager(@PathParam("id") String id) {
        AuthResult auth = requireCapability(MANAGE_USERS_ROLE);
        String username;
        try {
            username = promoteManager(auth.user(), id);
        } catch (NoSuchElementException e) {
            throw new NotFoundException(e.getMessage());
        } catch (IllegalStateException e) {
            throw new ClientErrorException(e.getMessage(), Response.Status.CONFLICT);
        } catch (SecurityException e) {
            throw new ForbiddenException(e.getMessage());
        }
        fireGroupMembershipEvent(auth, id, username, SCOUT_USER_MANAGER_GROUP, OperationType.CREATE);
        return ok("granted", username);
    }

    /** Revoke the delegated user-manager role (manage-users capability; an admin target requires an admin caller). */
    @DELETE
    @Path("users/{id}/manager")
    @Produces(MediaType.APPLICATION_JSON)
    public Response demoteUserManager(@PathParam("id") String id) {
        AuthResult auth = requireCapability(MANAGE_USERS_ROLE);
        String username;
        try {
            username = demoteManager(auth.user(), id);
        } catch (NoSuchElementException e) {
            throw new NotFoundException(e.getMessage());
        } catch (SecurityException e) {
            throw new ForbiddenException(e.getMessage());
        }
        fireGroupMembershipEvent(auth, id, username, SCOUT_USER_MANAGER_GROUP, OperationType.DELETE);
        return ok("revoked", username);
    }

    /** Offboard a user: remove all Scout group membership. Blocks self-offboard (409). manage-users capability. */
    @DELETE
    @Path("users/{id}/membership")
    @Produces(MediaType.APPLICATION_JSON)
    public Response offboardUser(@PathParam("id") String id) {
        AuthResult auth = requireCapability(MANAGE_USERS_ROLE);
        OffboardResult result;
        try {
            result = offboard(auth.user(), id);
        } catch (NoSuchElementException e) {
            throw new NotFoundException(e.getMessage());
        } catch (IllegalStateException e) {
            throw new ClientErrorException(e.getMessage(), Response.Status.CONFLICT);
        } catch (SecurityException e) {
            throw new ForbiddenException(e.getMessage());
        }
        fireGroupMembershipEvent(auth, id, result.username(), SCOUT_USER_GROUP, OperationType.DELETE);
        if (result.wasAdmin()) {
            fireGroupMembershipEvent(auth, id, result.username(), SCOUT_ADMIN_GROUP, OperationType.DELETE);
        }
        if (result.wasManager()) {
            fireGroupMembershipEvent(auth, id, result.username(), SCOUT_USER_MANAGER_GROUP, OperationType.DELETE);
        }
        return ok("offboarded", result.username());
    }

    AuthResult requireScoutAdmin() {
        AuthResult auth = authenticate();
        if (!isScoutAdmin(auth.user())) {
            throw new ForbiddenException("requires membership in " + SCOUT_ADMIN_GROUP);
        }
        return auth;
    }

    /** Authorize by capability (ADR 0026): the caller must hold the named realm role. */
    AuthResult requireCapability(String roleName) {
        AuthResult auth = authenticate();
        if (!hasCapability(auth.user(), roleName)) {
            throw new ForbiddenException("requires the " + roleName + " role");
        }
        return auth;
    }

    private AuthResult authenticate() {
        AuthResult auth = new AppAuthManager.BearerTokenAuthenticator(session).authenticate();
        if (auth == null || auth.user() == null) {
            throw new NotAuthorizedException("Bearer");
        }
        // Defense in depth: only accept tokens audienced for this API (see
        // REQUIRED_AUDIENCE). This API can grant scout-admin (-> realm-admin), so a
        // bearer minted for another resource — e.g. a scout-admin's aud=trino
        // notebook token — must not reach it even though that user is an admin.
        // Keycloak's own admin console is the escape hatch for everything else.
        if (!hasRequiredAudience(auth.token() == null ? null : auth.token().getAudience())) {
            throw new ForbiddenException("token not audienced for " + REQUIRED_AUDIENCE);
        }
        return auth;
    }

    /**
     * Emit a {@code GROUP_MEMBERSHIP} admin event for a user's group change.
     * The SPI mutates the user model directly (no admin REST call), so without
     * this the change is invisible to the realm's admin-event listeners: the OPA
     * bundle publisher re-snapshots the user on this event (so the grant actually
     * reaches Trino), the approval/offboard email listener keys off
     * {@code scout-user} in the representation and reads the {@code username}
     * from the event detail, and the action lands in the admin-events audit log.
     * The {@code users/{id}/...} resourcePath is what the publisher's user-id
     * extractor reads. The {@code username} detail mirrors what Keycloak's own
     * group-membership events carry; the email listener requires it.
     */
    private void fireGroupMembershipEvent(AuthResult auth, String userId, String username,
                                          String groupName, OperationType op) {
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
                .detail("username", username)
                .success();
    }

    /** Emit a {@code USER}/{@code UPDATE} admin event so an attribute-only change re-snapshots to OPA + audits. */
    private void fireUserUpdatedEvent(AuthResult auth, String userId, String username) {
        RealmModel realm = session.getContext().getRealm();
        adminEvent(auth, realm)
                .operation(OperationType.UPDATE)
                .resource(ResourceType.USER)
                .resourcePath("users", userId)
                .detail("username", username)
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
        return inGroup(user, SCOUT_ADMIN_GROUP);
    }

    boolean isUserManager(UserModel user) {
        return inGroup(user, SCOUT_USER_MANAGER_GROUP);
    }

    private boolean inGroup(UserModel user, String groupName) {
        return user.getGroupsStream().anyMatch(g -> groupName.equals(g.getName()));
    }

    /**
     * Whether the user holds the named realm role, directly or via any of their
     * groups' role mappings. Resolved explicitly rather than through
     * {@link UserModel#hasRole} so the logic is deterministic and unit-testable
     * with plain mocks. Composites are intentionally not expanded — capability
     * roles are mapped straight onto groups in the realm template.
     */
    boolean hasCapability(UserModel user, String roleName) {
        RoleModel role = session.getContext().getRealm().getRole(roleName);
        if (role == null) {
            return false;
        }
        String roleId = role.getId();
        return user.getRoleMappingsStream().anyMatch(r -> roleId.equals(r.getId()))
                || user.getGroupsStream().anyMatch(
                        g -> g.getRoleMappingsStream().anyMatch(r -> roleId.equals(r.getId())));
    }

    /**
     * Managers may not modify admins: every manager-tier mutation rejects a
     * target in scout-admin unless the caller is one too (SecurityException,
     * translated to 403 by the adapters). One shared rule instead of
     * per-endpoint special cases, so "can never touch scout-admin" stays a
     * single invariant as more delegated roles arrive.
     */
    void requireCanModifyTarget(UserModel actor, UserModel target) {
        if (isScoutAdmin(target) && (actor == null || !isScoutAdmin(actor))) {
            throw new SecurityException(
                    "only a " + SCOUT_ADMIN_GROUP + " can modify " + target.getUsername());
        }
    }

    // Whether the bearer carries aud=scout-users (added by the launchpad client's
    // audience mapper). Keycloak's BearerTokenAuthenticator validates the token but
    // not a specific audience, so the SPI checks it.
    boolean hasRequiredAudience(String[] audiences) {
        if (audiences == null) {
            return false;
        }
        for (String aud : audiences) {
            if (REQUIRED_AUDIENCE.equals(aud)) {
                return true;
            }
        }
        return false;
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
     * and join scout-user. Throws {@link IllegalArgumentException} (bad request),
     * {@link NoSuchElementException} (not found), {@link IllegalStateException}
     * (already approved — keeps approve idempotent so a re-approve doesn't clobber
     * attributes or re-fire the grant event/email), or {@link SecurityException}
     * (admin target, non-admin actor) — translated to HTTP by the
     * {@link #approve} adapter. Validates everything before writing anything, so
     * a bad request never leaves a half-applied grant.
     */
    String applyApproval(UserModel actor, ApproveRequest req) {
        RealmModel realm = session.getContext().getRealm();
        UserModel user = requireUser(realm, req == null ? null : req.userId());
        requireCanModifyTarget(actor, user);
        if (user.getGroupsStream().anyMatch(g -> SCOUT_USER_GROUP.equals(g.getName()))) {
            throw new IllegalStateException(user.getUsername() + " is already approved");
        }
        Map<String, List<String>> attributes = validateAttributes(req.attributes());
        GroupModel scoutUser = requireGroup(realm, SCOUT_USER_GROUP);
        attributes.forEach(user::setAttribute);
        user.joinGroup(scoutUser);
        log.infof("scout-users: approved %s (attributes set: %s)",
                user.getUsername(), attributes.keySet());
        return user.getUsername();
    }

    /** Enforce the attribute's own constraints (length / options / pattern / cardinality). */
    void validate(UPAttribute attr, List<String> values) {
        if (!attr.isMultivalued() && values.size() > 1) {
            throw new IllegalArgumentException(attr.getName() + " is single-valued");
        }
        // Honor the realm's length:{max} so this SPI and Keycloak's own write path
        // (LengthValidator via UserProfile) agree on what's valid. The realm template
        // renders only `max`, applied to every value regardless of options/pattern.
        Integer max = maxLength(attr);
        if (max != null) {
            for (String v : values) {
                if (v.length() > max) {
                    throw new IllegalArgumentException(
                            attr.getName() + ": '" + v + "' exceeds the maximum length of " + max);
                }
            }
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
        // Resolve the scoutAuthz key set once per request, not once per user.
        Set<String> authzKeys = authzAttributes().keySet();
        return session.users().searchForUserStream(realm, params)
                .map(u -> toScoutUser(u, authzKeys))
                .filter(u -> statusMatches(u, status))
                .toList();
    }

    /** Project a user to the console view: derived status, admin flag, and the scoutAuthz attributes only. */
    ScoutUser toScoutUser(UserModel u) {
        return toScoutUser(u, authzAttributes().keySet());
    }

    /** As {@link #toScoutUser(UserModel)}, with the scoutAuthz key set passed in so a list call resolves it once. */
    ScoutUser toScoutUser(UserModel u, Set<String> authzKeys) {
        List<String> groups = u.getGroupsStream().map(GroupModel::getName).toList();
        boolean admin = groups.contains(SCOUT_ADMIN_GROUP);
        boolean manager = groups.contains(SCOUT_USER_MANAGER_GROUP);
        boolean active = admin || groups.contains(SCOUT_USER_GROUP);
        boolean termsAccepted = u.getFirstAttribute(TERMS_ACCEPTED_ATTR) != null;
        String status = admin ? "admin" : active ? "active" : termsAccepted ? "pending" : "none";

        Map<String, List<String>> all = u.getAttributes();
        Map<String, List<String>> attrs = new LinkedHashMap<>();
        for (String key : authzKeys) {
            List<String> values = all == null ? null : all.get(key);
            if (values != null && !values.isEmpty()) {
                attrs.put(key, values);
            }
        }
        return new ScoutUser(u.getId(), u.getUsername(), u.getEmail(), fullName(u), status, admin, manager, attrs);
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
    String setAttributes(UserModel actor, String userId, Map<String, List<String>> attributes) {
        RealmModel realm = session.getContext().getRealm();
        UserModel user = requireUser(realm, userId);
        requireCanModifyTarget(actor, user);
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

    /** Demote a user from scout-admin (scout-admin only). Self-demote is allowed. */
    String demote(String userId) {
        RealmModel realm = session.getContext().getRealm();
        UserModel user = requireUser(realm, userId);
        GroupModel admin = requireGroup(realm, SCOUT_ADMIN_GROUP);
        user.leaveGroup(admin);
        log.infof("scout-users: demoted %s from %s", user.getUsername(), SCOUT_ADMIN_GROUP);
        return user.getUsername();
    }

    /**
     * Grant the delegated user-manager role (idempotent). The target must
     * already be an approved scout-user (IllegalStateException -> 409): the
     * manager group is additive, granting only the manage-users capability, so
     * a manager who isn't a scout-user would be locked out at the ingress with
     * a role that does nothing.
     */
    String promoteManager(UserModel actor, String userId) {
        RealmModel realm = session.getContext().getRealm();
        UserModel user = requireUser(realm, userId);
        requireCanModifyTarget(actor, user);
        if (!inGroup(user, SCOUT_USER_GROUP)) {
            throw new IllegalStateException(user.getUsername() + " is not an approved " + SCOUT_USER_GROUP
                    + "; approve them before granting " + SCOUT_USER_MANAGER_GROUP);
        }
        user.joinGroup(requireGroup(realm, SCOUT_USER_MANAGER_GROUP));
        log.infof("scout-users: granted %s to %s", SCOUT_USER_MANAGER_GROUP, user.getUsername());
        return user.getUsername();
    }

    /** Revoke the delegated user-manager role (idempotent). Self-revoke is allowed (a privilege drop). */
    String demoteManager(UserModel actor, String userId) {
        RealmModel realm = session.getContext().getRealm();
        UserModel user = requireUser(realm, userId);
        requireCanModifyTarget(actor, user);
        user.leaveGroup(requireGroup(realm, SCOUT_USER_MANAGER_GROUP));
        log.infof("scout-users: revoked %s from %s", SCOUT_USER_MANAGER_GROUP, user.getUsername());
        return user.getUsername();
    }

    /**
     * Offboard a user: remove scout-user and (if present) scout-user-manager and
     * scout-admin. Blocks offboarding yourself (IllegalStateException); an admin
     * target requires an admin actor (SecurityException).
     * {@return what was removed, so the adapter can emit the matching events}.
     */
    OffboardResult offboard(UserModel actor, String userId) {
        if (actor != null && actor.getId() != null && actor.getId().equals(userId)) {
            throw new IllegalStateException("cannot offboard yourself");
        }
        RealmModel realm = session.getContext().getRealm();
        UserModel user = requireUser(realm, userId);
        requireCanModifyTarget(actor, user);
        boolean wasAdmin = isScoutAdmin(user);
        boolean wasManager = isUserManager(user);
        GroupModel scoutUser = topLevelGroup(realm, SCOUT_USER_GROUP);
        if (scoutUser != null) {
            user.leaveGroup(scoutUser);
        }
        if (wasManager) {
            GroupModel manager = topLevelGroup(realm, SCOUT_USER_MANAGER_GROUP);
            if (manager != null) {
                user.leaveGroup(manager);
            }
        }
        if (wasAdmin) {
            GroupModel scoutAdmin = topLevelGroup(realm, SCOUT_ADMIN_GROUP);
            if (scoutAdmin != null) {
                user.leaveGroup(scoutAdmin);
            }
        }
        log.infof("scout-users: offboarded %s (wasAdmin=%s, wasManager=%s)",
                user.getUsername(), wasAdmin, wasManager);
        return new OffboardResult(user.getUsername(), wasAdmin, wasManager);
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

    private Integer maxLength(UPAttribute attr) {
        Map<String, Object> length = validation(attr, "length");
        Object max = length == null ? null : length.get("max");
        if (max == null) {
            return null;
        }
        if (max instanceof Number n) {
            return n.intValue();
        }
        try {
            return Integer.valueOf(max.toString().trim());
        } catch (NumberFormatException e) {
            return null;
        }
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
