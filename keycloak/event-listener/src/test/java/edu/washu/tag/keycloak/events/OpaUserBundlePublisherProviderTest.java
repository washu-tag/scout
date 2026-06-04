package edu.washu.tag.keycloak.events;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.List;
import java.util.Map;
import java.util.stream.Stream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.keycloak.events.admin.AdminEvent;
import org.keycloak.events.admin.OperationType;
import org.keycloak.events.admin.ResourceType;
import org.keycloak.models.GroupModel;
import org.keycloak.models.KeycloakContext;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.RealmProvider;
import org.keycloak.models.UserModel;
import org.keycloak.models.UserProvider;

/**
 * Routing tests for the per-session event listener. The Provider's only job is
 * to translate Keycloak admin events into the factory's upsert/remove mutation
 * calls; this verifies which events route where (and which are correctly
 * ignored) plus the resourcePath -> userId extraction. The factory is a mock —
 * we assert on the calls the Provider makes, not on the snapshot bookkeeping
 * (covered by OpaUserBundlePublisherProviderFactoryTest).
 */
class OpaUserBundlePublisherProviderTest {

    private KeycloakSession session;
    private OpaUserBundlePublisherProviderFactory factory;
    private OpaUserBundlePublisherProvider provider;

    @BeforeEach
    void setUp() {
        session = mock(KeycloakSession.class);
        when(session.getContext()).thenReturn(mock(KeycloakContext.class));
        factory = mock(OpaUserBundlePublisherProviderFactory.class);
        when(factory.isEnabled()).thenReturn(true);
        provider = new OpaUserBundlePublisherProvider(session, factory);
    }

    private AdminEvent adminEvent(ResourceType rt, OperationType op, String path,
                                  String realmId, Map<String, String> details) {
        AdminEvent e = mock(AdminEvent.class);
        when(e.getResourceType()).thenReturn(rt);
        when(e.getOperationType()).thenReturn(op);
        when(e.getResourcePath()).thenReturn(path);
        when(e.getRealmId()).thenReturn(realmId);
        when(e.getDetails()).thenReturn(details);
        return e;
    }

    // Wire session -> realm -> user so upsertFromCurrentState resolves a user.
    private void stubUser(String realmId, String userId, String username, boolean enabled,
                          List<String> groupNames, Map<String, List<String>> attributes) {
        RealmModel realm = mock(RealmModel.class);
        RealmProvider realms = mock(RealmProvider.class);
        when(session.realms()).thenReturn(realms);
        when(realms.getRealm(realmId)).thenReturn(realm);
        UserProvider users = mock(UserProvider.class);
        when(session.users()).thenReturn(users);
        UserModel user = mock(UserModel.class);
        when(users.getUserById(realm, userId)).thenReturn(user);
        when(user.getUsername()).thenReturn(username);
        when(user.isEnabled()).thenReturn(enabled);
        Stream<GroupModel> groups = groupNames.stream().map(name -> {
            GroupModel g = mock(GroupModel.class);
            when(g.getName()).thenReturn(name);
            return g;
        });
        when(user.getGroupsStream()).thenReturn(groups);
        when(user.getAttributes()).thenReturn(attributes);
    }

    private void verifyNoMutation() {
        verify(factory, never()).upsertUser(any(), any(), anyBoolean(), anyList(), anyMap());
        verify(factory, never()).removeUser(any(), any());
    }

    @Test
    void user_create_upserts_snapshot() {
        stubUser("realm-1", "u-1", "alice", true, List.of("scout-user"),
                Map.of("allowed_facilities", List.of("WUSM")));
        provider.onEvent(adminEvent(ResourceType.USER, OperationType.CREATE,
                "users/u-1", "realm-1", null), false);
        verify(factory).upsertUser("u-1", "alice", true, List.of("scout-user"),
                Map.of("allowed_facilities", List.of("WUSM")));
    }

    @Test
    void user_delete_removes_snapshot_with_event_username() {
        provider.onEvent(adminEvent(ResourceType.USER, OperationType.DELETE,
                "users/u-1", "realm-1", Map.of("username", "alice")), false);
        verify(factory).removeUser("u-1", "alice");
    }

    @Test
    void group_membership_create_reupserts_user() {
        // Propagation requirement (ADR 0021): adding a user to scout-user must
        // re-snapshot the user so the rego approval gate sees the group.
        stubUser("realm-1", "u-1", "alice", true, List.of("scout-user"), Map.of());
        provider.onEvent(adminEvent(ResourceType.GROUP_MEMBERSHIP, OperationType.CREATE,
                "users/u-1/groups/g-1", "realm-1", null), false);
        verify(factory).upsertUser(eq("u-1"), eq("alice"), anyBoolean(), anyList(), anyMap());
    }

    @Test
    void group_membership_delete_reupserts_user() {
        // Removing from scout-user must propagate too (re-snapshot with the
        // smaller group list), or a deauthorized user keeps access.
        stubUser("realm-1", "u-1", "alice", true, List.of(), Map.of());
        provider.onEvent(adminEvent(ResourceType.GROUP_MEMBERSHIP, OperationType.DELETE,
                "users/u-1/groups/g-1", "realm-1", null), false);
        verify(factory).upsertUser(eq("u-1"), eq("alice"), anyBoolean(), anyList(), anyMap());
    }

    @Test
    void user_action_op_is_ignored() {
        // ACTION (password reset, etc.) doesn't change AuthZ attributes.
        provider.onEvent(adminEvent(ResourceType.USER, OperationType.ACTION,
                "users/u-1", "realm-1", null), false);
        verifyNoMutation();
    }

    @Test
    void non_user_non_group_event_with_user_path_ignored() {
        // A non-USER/non-GROUP_MEMBERSHIP event that carries a users/-shaped
        // resourcePath (e.g. a realm-role-mapping change) must be dropped by
        // the resource-type filter. extractUserId would happily parse the id
        // out of the path, so the type filter is the only thing preventing a
        // spurious re-snapshot here — a CLIENT/clients-path event wouldn't
        // exercise it (extractUserId rejects that path anyway).
        stubUser("realm-1", "u-1", "alice", true, List.of("scout-user"), Map.of());
        provider.onEvent(adminEvent(ResourceType.REALM_ROLE_MAPPING, OperationType.CREATE,
                "users/u-1/role-mappings/realm", "realm-1", null), false);
        verifyNoMutation();
    }

    @Test
    void events_ignored_when_factory_disabled() {
        when(factory.isEnabled()).thenReturn(false);
        provider.onEvent(adminEvent(ResourceType.USER, OperationType.CREATE,
                "users/u-1", "realm-1", null), false);
        verifyNoMutation();
    }

    @Test
    void role_mapping_suffix_stripped_from_user_id() {
        // resourcePath "users/{id}/role-mappings/..." must extract just {id}.
        stubUser("realm-1", "u-1", "alice", true, List.of("scout-user"), Map.of());
        provider.onEvent(adminEvent(ResourceType.USER, OperationType.UPDATE,
                "users/u-1/role-mappings/realm", "realm-1", null), false);
        verify(factory).upsertUser(eq("u-1"), eq("alice"), anyBoolean(), anyList(), anyMap());
    }

    @Test
    void non_users_resource_path_ignored() {
        // extractUserId returns null for a path that isn't under users/.
        provider.onEvent(adminEvent(ResourceType.GROUP_MEMBERSHIP, OperationType.CREATE,
                "groups/g-1/children", "realm-1", null), false);
        verifyNoMutation();
    }

    @Test
    void unresolvable_user_does_not_upsert() {
        // USER CREATE but the row can't be resolved (e.g., already gone): no write.
        RealmModel realm = mock(RealmModel.class);
        RealmProvider realms = mock(RealmProvider.class);
        when(session.realms()).thenReturn(realms);
        when(realms.getRealm("realm-1")).thenReturn(realm);
        UserProvider users = mock(UserProvider.class);
        when(session.users()).thenReturn(users);
        when(users.getUserById(realm, "u-1")).thenReturn(null);
        provider.onEvent(adminEvent(ResourceType.USER, OperationType.CREATE,
                "users/u-1", "realm-1", null), false);
        verify(factory, never()).upsertUser(any(), any(), anyBoolean(), anyList(), anyMap());
    }
}
