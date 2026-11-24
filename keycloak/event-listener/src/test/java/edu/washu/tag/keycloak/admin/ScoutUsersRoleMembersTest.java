package edu.washu.tag.keycloak.admin;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.keycloak.models.ClientModel;
import org.keycloak.models.GroupModel;
import org.keycloak.models.GroupProvider;
import org.keycloak.models.KeycloakContext;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.RoleModel;
import org.keycloak.models.UserModel;
import org.keycloak.models.UserProvider;

/**
 * The role-members lookup behind role-addressed launchpad notices (ADR 0043).
 * Keycloak's own role-members query returns direct mappings only, so these pin
 * the other ways a user holds a role: a group, a subgroup of one, and a composite.
 * Whether one role contains another is Keycloak's answer ({@code RoleModel.hasRole}).
 */
class ScoutUsersRoleMembersTest {

    private RealmModel realm;
    private UserProvider users;
    private GroupProvider groups;
    private ScoutUsersResource resource;

    private ClientModel tracker;
    private RoleModel reviewer;
    private final List<RoleModel> realmRoles = new ArrayList<>();
    private final List<RoleModel> trackerRoles = new ArrayList<>();
    private final Map<RoleModel, List<UserModel>> directMembers = new HashMap<>();
    private final Map<RoleModel, List<GroupModel>> groupsByRole = new HashMap<>();
    private final Map<GroupModel, List<UserModel>> groupMembers = new HashMap<>();

    @BeforeEach
    void setUp() {
        realm = mock(RealmModel.class);
        users = mock(UserProvider.class);
        groups = mock(GroupProvider.class);
        KeycloakContext context = mock(KeycloakContext.class);
        when(context.getRealm()).thenReturn(realm);
        KeycloakSession session = mock(KeycloakSession.class);
        when(session.getContext()).thenReturn(context);
        when(session.users()).thenReturn(users);
        when(session.groups()).thenReturn(groups);

        tracker = client("request-tracker", "tracker-uuid");
        reviewer = role("reviewer");
        trackerRoles.add(reviewer);
        trackerRoles.add(role("unrelated"));
        when(tracker.getRole("reviewer")).thenReturn(reviewer);
        when(tracker.getRolesStream()).thenAnswer(i -> trackerRoles.stream());
        when(realm.getClientByClientId("request-tracker")).thenReturn(tracker);
        when(realm.getClientsStream()).thenAnswer(i -> Stream.of(tracker));
        when(realm.getRolesStream()).thenAnswer(i -> realmRoles.stream());

        when(users.getRoleMembersStream(eq(realm), any(RoleModel.class)))
                .thenAnswer(i -> directMembers.getOrDefault(i.getArgument(1), List.of()).stream());
        when(groups.getGroupsByRoleStream(eq(realm), any(RoleModel.class), any(), any()))
                .thenAnswer(i -> groupsByRole.getOrDefault(i.getArgument(1), List.of()).stream());
        when(users.getGroupMembersStream(eq(realm), any(GroupModel.class)))
                .thenAnswer(i -> groupMembers.getOrDefault(i.getArgument(1), List.of()).stream());

        resource = new ScoutUsersResource(session);
    }

    private ClientModel client(String clientId, String id) {
        ClientModel c = mock(ClientModel.class);
        when(c.getClientId()).thenReturn(clientId);
        when(c.getId()).thenReturn(id);
        return c;
    }

    private RoleModel role(String name) {
        RoleModel r = mock(RoleModel.class);
        when(r.getName()).thenReturn(name);
        return r;
    }

    // A composite that contains `contains`, as Keycloak would answer hasRole.
    private RoleModel composite(String name, RoleModel contains) {
        RoleModel r = role(name);
        when(r.isComposite()).thenReturn(true);
        when(r.hasRole(contains)).thenReturn(true);
        return r;
    }

    private UserModel user(String username) {
        UserModel u = mock(UserModel.class);
        when(u.getUsername()).thenReturn(username);
        when(u.isEnabled()).thenReturn(true);
        return u;
    }

    private GroupModel group(String id, GroupModel... children) {
        GroupModel g = mock(GroupModel.class);
        when(g.getId()).thenReturn(id);
        when(g.getSubGroupsStream()).thenAnswer(i -> Stream.of(children));
        return g;
    }

    private List<String> members(int max) {
        return resource.findRoleMembers("request-tracker", "reviewer", max).usernames();
    }

    @Test
    void direct_mapping() {
        directMembers.put(reviewer, List.of(user("alice")));
        assertEquals(List.of("alice"), members(10));
    }

    @Test
    void group_mapping_reaches_subgroups_not_parents() {
        GroupModel leads = group("leads");
        GroupModel reviewers = group("reviewers", leads);
        groupsByRole.put(reviewer, List.of(reviewers));
        groupMembers.put(reviewers, List.of(user("bob")));
        groupMembers.put(leads, List.of(user("carol")));
        GroupModel department = group("department", reviewers);
        groupMembers.put(department, List.of(user("dave")));

        assertEquals(List.of("bob", "carol"), members(10));
    }

    @Test
    void composite_realm_role_on_group_as_fragment_grants_reach_users() {
        RoleModel scoutAdmin = composite("scout-admin", reviewer);
        realmRoles.add(scoutAdmin);
        realmRoles.add(role("scout-user"));
        GroupModel admins = group("scout-admin");
        groupsByRole.put(scoutAdmin, List.of(admins));
        groupMembers.put(admins, List.of(user("erin")));

        assertEquals(List.of("erin"), members(10));
    }

    @Test
    void composites_mapped_directly_client_or_realm() {
        RoleModel lead = composite("lead", reviewer);
        RoleModel chief = composite("chief", reviewer);
        trackerRoles.add(lead);
        realmRoles.add(chief);
        directMembers.put(lead, List.of(user("frank")));
        directMembers.put(chief, List.of(user("grace")));

        assertEquals(List.of("frank", "grace"), members(10));
    }

    @Test
    void each_user_once_however_many_ways_they_hold_it() {
        UserModel alice = user("alice");
        RoleModel lead = composite("lead", reviewer);
        trackerRoles.add(lead);
        GroupModel reviewers = group("reviewers");
        directMembers.put(reviewer, List.of(alice));
        directMembers.put(lead, List.of(alice));
        groupsByRole.put(reviewer, List.of(reviewers));
        groupsByRole.put(lead, List.of(reviewers));
        groupMembers.put(reviewers, List.of(alice));

        assertEquals(List.of("alice"), members(10));
    }

    @Test
    void disabled_users_and_service_accounts_are_left_out() {
        UserModel disabled = user("heidi");
        when(disabled.isEnabled()).thenReturn(false);
        UserModel serviceAccount = user("service-account-request-tracker");
        when(serviceAccount.getServiceAccountClientLink()).thenReturn("tracker-uuid");
        directMembers.put(reviewer, List.of(disabled, serviceAccount, user("ivan")));

        assertEquals(List.of("ivan"), members(10));
    }

    @Test
    void usernames_are_lowercased_and_sorted() {
        directMembers.put(reviewer, List.of(user("Zoe"), user("ALICE")));
        assertEquals(List.of("alice", "zoe"), members(10));
    }

    @Test
    void more_members_than_max_is_truncated() {
        directMembers.put(reviewer, List.of(user("a"), user("b"), user("c")));

        ScoutUsersResource.RoleMembers capped = resource.findRoleMembers("request-tracker", "reviewer", 2);
        assertEquals(2, capped.usernames().size());
        assertTrue(capped.truncated());

        ScoutUsersResource.RoleMembers exact = resource.findRoleMembers("request-tracker", "reviewer", 3);
        assertEquals(List.of("a", "b", "c"), exact.usernames());
        assertFalse(exact.truncated());
    }

    @Test
    void no_members_is_empty_not_missing() {
        ScoutUsersResource.RoleMembers none = resource.findRoleMembers("request-tracker", "reviewer", 10);
        assertEquals(List.of(), none.usernames());
        assertFalse(none.truncated());
    }

    @Test
    void an_unknown_client_or_role_is_null() {
        assertNull(resource.findRoleMembers("no-such-client", "reviewer", 10));
        assertNull(resource.findRoleMembers("request-tracker", "no-such-role", 10));
    }

    @Test
    void only_the_launchpad_service_account_may_ask() {
        ClientModel launchpad = client("launchpad", "launchpad-uuid");
        UserModel launchpadServiceAccount = user("service-account-launchpad");
        when(launchpadServiceAccount.getServiceAccountClientLink()).thenReturn("launchpad-uuid");
        UserModel signedInUser = user("alice");
        UserModel trackerServiceAccount = user("service-account-request-tracker");
        when(trackerServiceAccount.getServiceAccountClientLink()).thenReturn("tracker-uuid");

        assertTrue(resource.isLaunchpadServiceAccount(launchpad, launchpadServiceAccount));
        assertFalse(resource.isLaunchpadServiceAccount(launchpad, signedInUser),
                "a user's own launchpad token is not the launchpad's");
        assertFalse(resource.isLaunchpadServiceAccount(tracker, trackerServiceAccount),
                "another client's service account");
        assertFalse(resource.isLaunchpadServiceAccount(launchpad, trackerServiceAccount));
        assertFalse(resource.isLaunchpadServiceAccount(null, launchpadServiceAccount));
    }
}
