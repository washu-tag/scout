package edu.washu.tag.keycloak.admin;

import jakarta.ws.rs.BadRequestException;
import jakarta.ws.rs.Consumes;
import jakarta.ws.rs.ForbiddenException;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.NotAuthorizedException;
import jakarta.ws.rs.NotFoundException;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
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
 * JAX-RS resource backing the Scout approval UI. All endpoints require a bearer
 * token whose user is in {@code scout-admin}; the resource then acts as that
 * admin (no standing admin-API credential).
 *
 * <p>The data-access attributes an admin sets at approval are <b>not hardcoded</b>
 * — they're discovered from the realm User Profile (attributes annotated
 * {@code scoutAuthz=true}), which is itself rendered from {@code trino_attribute_filters}.
 * So {@link #buildSchema()} drives the UI form and {@link #applyApproval} validates
 * against whatever dimensions are configured: add a dimension in inventory and it
 * flows through with no change here.
 *
 * <p>Structure: the {@code @GET}/{@code @POST} methods are thin adapters that
 * authenticate and translate domain exceptions to HTTP status; the core logic
 * ({@link #buildSchema}, {@link #findPending}, {@link #applyApproval},
 * {@link #isScoutAdmin}, {@link #validate}) is plain and unit-tested directly.
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

    /** Approval request body: the user and the data-access attribute values to grant. */
    public record ApproveRequest(String userId, Map<String, List<String>> attributes) {
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
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "approved");
        body.put("username", username);
        return Response.ok(body).build();
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

    private AdminEventBuilder adminEvent(AuthResult auth, RealmModel realm) {
        AdminAuth adminAuth = new AdminAuth(realm, auth.getToken(), auth.getUser(), auth.getClient());
        return new AdminEventBuilder(realm, adminAuth, session, session.getContext().getConnection());
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
        if (req == null || req.userId() == null || req.userId().isBlank()) {
            throw new IllegalArgumentException("userId is required");
        }
        RealmModel realm = session.getContext().getRealm();
        UserModel user = session.users().getUserById(realm, req.userId());
        if (user == null) {
            throw new NoSuchElementException("user not found: " + req.userId());
        }

        Map<String, UPAttribute> allowed = authzAttributes();
        Map<String, List<String>> attributes = req.attributes() == null ? Map.of() : req.attributes();
        for (Map.Entry<String, List<String>> e : attributes.entrySet()) {
            UPAttribute attr = allowed.get(e.getKey());
            if (attr == null) {
                throw new IllegalArgumentException("not a Scout data-access attribute: " + e.getKey());
            }
            validate(attr, e.getValue() == null ? List.of() : e.getValue());
        }

        GroupModel scoutUser = topLevelGroup(realm, SCOUT_USER_GROUP);
        if (scoutUser == null) {
            throw new NoSuchElementException("group not found: " + SCOUT_USER_GROUP);
        }

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
