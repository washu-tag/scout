package edu.washu.tag.keycloak.events;

import java.util.List;
import java.util.stream.Collectors;
import org.jboss.logging.Logger;
import org.keycloak.events.Event;
import org.keycloak.events.EventListenerProvider;
import org.keycloak.events.admin.AdminEvent;
import org.keycloak.events.admin.OperationType;
import org.keycloak.events.admin.ResourceType;
import org.keycloak.models.GroupModel;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserModel;

/**
 * Per-session event listener that forwards Keycloak admin user changes to
 * {@link OpaUserBundlePublisherProviderFactory}'s in-memory snapshot. The
 * factory is responsible for debouncing the resulting publish to MinIO;
 * this class only translates between Keycloak's event model and the
 * factory's mutation API.
 *
 * <p>Events handled:
 * <ul>
 *   <li>{@link OperationType#CREATE CREATE} / {@link OperationType#UPDATE UPDATE}
 *       on a {@link ResourceType#USER USER} — read the user row and
 *       upsert into the snapshot.</li>
 *   <li>{@link OperationType#DELETE DELETE} on a {@link ResourceType#USER USER}
 *       — remove the user from the snapshot.</li>
 * </ul>
 * All other events (user-level login/logout/password-reset, non-USER
 * admin events) are no-ops — they don't change RBAC-relevant state.
 */
public class OpaUserBundlePublisherProvider implements EventListenerProvider {

    private static final Logger log = Logger.getLogger(OpaUserBundlePublisherProvider.class);

    private final KeycloakSession session;
    private final OpaUserBundlePublisherProviderFactory factory;

    OpaUserBundlePublisherProvider(KeycloakSession session,
                                   OpaUserBundlePublisherProviderFactory factory) {
        this.session = session;
        this.factory = factory;
    }

    @Override
    public void onEvent(Event event) {
        // User-level events don't carry RBAC-relevant state changes.
        // Attribute updates happen via the admin API and are handled
        // in the AdminEvent path below.
    }

    @Override
    public void onEvent(AdminEvent event, boolean includeRepresentation) {
        if (!factory.isEnabled()) {
            return;
        }
        ResourceType rt = event.getResourceType();
        // USER covers attribute changes; GROUP_MEMBERSHIP covers add/remove
        // from groups (e.g., scout-user), which the rego policy's approval
        // gate reads from `user_attrs.groups`. Without handling the latter,
        // dropping a user from scout-user wouldn't propagate to the bundle.
        if (rt != ResourceType.USER && rt != ResourceType.GROUP_MEMBERSHIP) {
            return;
        }
        OperationType op = event.getOperationType();
        String userId = extractUserId(event.getResourcePath());
        if (userId == null) {
            return;
        }

        if (rt == ResourceType.GROUP_MEMBERSHIP) {
            // CREATE = added to group, DELETE = removed. Either way, re-snapshot
            // the user with its current group list.
            if (op == OperationType.CREATE || op == OperationType.DELETE) {
                upsertFromCurrentState(event.getRealmId(), userId);
            }
            return;
        }

        switch (op) {
            case CREATE, UPDATE -> upsertFromCurrentState(event.getRealmId(), userId);
            case DELETE -> handleDelete(event, userId);
            default -> {
                // ACTION (password reset, etc.) doesn't change attributes;
                // skip to keep publish noise down.
            }
        }
    }

    @Override
    public void close() {
    }

    private void upsertFromCurrentState(String realmId, String userId) {
        RealmModel realm = session.realms().getRealm(realmId);
        if (realm == null) {
            return;
        }
        // The user provider's validateUser path needs a realm-bound
        // session context (InfinispanOrganizationProvider.getRealm).
        // AdminEvent listeners receive a session whose context isn't
        // guaranteed to have the realm set — bind it explicitly so
        // getUserById doesn't throw mid-call.
        session.getContext().setRealm(realm);
        UserModel user = session.users().getUserById(realm, userId);
        if (user == null || user.getUsername() == null) {
            return;
        }
        List<String> groups = user.getGroupsStream()
                .map(GroupModel::getName)
                .collect(Collectors.toList());
        factory.upsertUser(user.getUsername(), user.isEnabled(), groups, user.getAttributes());
    }

    private void handleDelete(AdminEvent event, String userId) {
        // The user row is gone by the time DELETE fires, so we read the
        // username from the event's details map — Keycloak puts it there
        // specifically for this case. If it's missing (e.g., a transport
        // that strips details), we have to log and skip: without a
        // username key we can't remove the right entry from the snapshot.
        String username = event.getDetails() != null ? event.getDetails().get("username") : null;
        if (username == null) {
            log.warnf("DELETE admin event for user %s has no username in details; cannot remove from OPA bundle",
                    userId);
            return;
        }
        factory.removeUser(username);
    }

    private static String extractUserId(String resourcePath) {
        if (resourcePath == null || !resourcePath.startsWith("users/")) {
            return null;
        }
        // resourcePath shape: "users/{userId}" or "users/{userId}/role-mappings/..."
        return resourcePath.substring("users/".length()).split("/", 2)[0];
    }
}
