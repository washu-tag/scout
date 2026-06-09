package edu.washu.tag.keycloak.admin;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.Set;
import java.util.stream.Stream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.keycloak.models.GroupModel;
import org.keycloak.models.KeycloakContext;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserModel;
import org.keycloak.models.UserProvider;
import org.keycloak.representations.userprofile.config.UPAttribute;
import org.keycloak.representations.userprofile.config.UPConfig;
import org.keycloak.userprofile.UserProfileProvider;

/**
 * Unit tests for the dynamic approval logic. The data-access attributes are
 * discovered from the User Profile (annotated scoutAuthz=true), so these build
 * a profile config and assert the schema/validation/approve paths adapt to it
 * rather than to a hardcoded attribute set. The thin JAX-RS endpoint wrappers
 * (auth + exception translation) are exercised by Keycloak at runtime; the core
 * methods tested here throw plain exceptions and need no JAX-RS runtime.
 */
class ScoutUsersResourceTest {

    private KeycloakSession session;
    private RealmModel realm;
    private UserProvider users;
    private GroupModel scoutUserGroup;
    private GroupModel scoutAdminGroup;
    private ScoutUsersResource resource;

    @BeforeEach
    void setUp() {
        session = mock(KeycloakSession.class);
        realm = mock(RealmModel.class);
        users = mock(UserProvider.class);

        KeycloakContext context = mock(KeycloakContext.class);
        when(session.getContext()).thenReturn(context);
        when(context.getRealm()).thenReturn(realm);
        when(session.users()).thenReturn(users);

        scoutUserGroup = group("scout-user");
        scoutAdminGroup = group("scout-admin");
        when(realm.getGroupsStream()).thenAnswer(i -> Stream.of(scoutUserGroup, scoutAdminGroup));

        UserProfileProvider upp = mock(UserProfileProvider.class);
        when(session.getProvider(UserProfileProvider.class)).thenReturn(upp);
        when(upp.getConfiguration()).thenReturn(profileConfig());

        resource = new ScoutUsersResource(session);
    }

    private GroupModel group(String name) {
        GroupModel g = mock(GroupModel.class);
        when(g.getName()).thenReturn(name);
        return g;
    }

    // allowed_facilities (multivalued, options incl. wildcard) + mask_phi_fields
    // (single-valued, true/false) are scoutAuthz; email is not.
    private UPConfig profileConfig() {
        UPAttribute facilities = new UPAttribute("allowed_facilities");
        facilities.setMultivalued(true);
        facilities.setAnnotations(annotations("multiselect"));
        facilities.setValidations(optionsValidation(List.of("WUSM", "BJH", "*")));

        UPAttribute mask = new UPAttribute("mask_phi_fields");
        mask.setMultivalued(false);
        Map<String, Object> maskAnnotations = annotations("select");
        maskAnnotations.put("scoutDefault", "true");
        mask.setAnnotations(maskAnnotations);
        mask.setValidations(optionsValidation(List.of("true", "false")));

        UPAttribute email = new UPAttribute("email"); // not scoutAuthz -> excluded

        UPConfig cfg = new UPConfig();
        cfg.setAttributes(List.of(facilities, mask, email));
        return cfg;
    }

    private Map<String, Object> annotations(String inputType) {
        Map<String, Object> ann = new HashMap<>();
        ann.put("scoutAuthz", "true");
        ann.put("inputType", inputType);
        return ann;
    }

    private Map<String, Map<String, Object>> optionsValidation(List<String> options) {
        return Map.of("options", Map.of("options", options));
    }

    private Map<String, Map<String, Object>> patternValidation(String pattern) {
        return Map.of("pattern", Map.of("pattern", pattern));
    }

    // --- schema: dynamic discovery -----------------------------------------

    @Test
    void schema_returns_only_scoutauthz_attributes_with_metadata() {
        List<ScoutUsersResource.AttrSchema> schema = resource.buildSchema();

        assertEquals(List.of("allowed_facilities", "mask_phi_fields"),
                schema.stream().map(ScoutUsersResource.AttrSchema::name).toList(),
                "email is not annotated scoutAuthz and must be excluded");
        var facilities = schema.get(0);
        assertTrue(facilities.multivalued());
        assertEquals("multiselect", facilities.inputType());
        assertEquals(List.of("WUSM", "BJH", "*"), facilities.options());
        assertNull(facilities.defaultValue(), "no scoutDefault annotation -> null default");

        var mask = schema.get(1);
        assertEquals("true", mask.defaultValue(), "mask carries its scoutDefault annotation");
    }

    // --- approve: validated against the introspected schema ----------------

    @Test
    void approve_sets_attributes_and_joins_scout_user() {
        UserModel target = mock(UserModel.class);
        when(target.getUsername()).thenReturn("alice");
        when(users.getUserById(realm, "u1")).thenReturn(target);

        resource.applyApproval(new ScoutUsersResource.ApproveRequest("u1",
                Map.of("allowed_facilities", List.of("WUSM"),
                        "mask_phi_fields", List.of("false"))));

        verify(target).setAttribute("allowed_facilities", List.of("WUSM"));
        verify(target).setAttribute("mask_phi_fields", List.of("false"));
        verify(target).joinGroup(scoutUserGroup);
    }

    @Test
    void approve_rejects_unknown_attribute_without_applying() {
        UserModel target = mock(UserModel.class);
        when(users.getUserById(realm, "u1")).thenReturn(target);

        assertThrows(IllegalArgumentException.class, () -> resource.applyApproval(
                new ScoutUsersResource.ApproveRequest("u1",
                        Map.of("not_an_authz_attr", List.of("x")))));
        verify(target, never()).setAttribute(any(), any());
        verify(target, never()).joinGroup(any());
    }

    @Test
    void approve_rejects_value_not_in_options() {
        UserModel target = mock(UserModel.class);
        when(users.getUserById(realm, "u1")).thenReturn(target);

        assertThrows(IllegalArgumentException.class, () -> resource.applyApproval(
                new ScoutUsersResource.ApproveRequest("u1",
                        Map.of("allowed_facilities", List.of("NOPE")))));
        verify(target, never()).joinGroup(any());
    }

    @Test
    void approve_rejects_multiple_values_for_single_valued() {
        UserModel target = mock(UserModel.class);
        when(users.getUserById(realm, "u1")).thenReturn(target);

        assertThrows(IllegalArgumentException.class, () -> resource.applyApproval(
                new ScoutUsersResource.ApproveRequest("u1",
                        Map.of("mask_phi_fields", List.of("true", "false")))));
        verify(target, never()).joinGroup(any());
    }

    @Test
    void approve_unknown_user_is_not_found() {
        when(users.getUserById(realm, "ghost")).thenReturn(null);
        assertThrows(NoSuchElementException.class, () -> resource.applyApproval(
                new ScoutUsersResource.ApproveRequest("ghost", Map.of())));
    }

    @Test
    void approve_already_approved_user_is_rejected_without_reapplying() {
        UserModel target = mock(UserModel.class);
        when(target.getUsername()).thenReturn("alice");
        when(target.getGroupsStream()).thenAnswer(i -> Stream.of(scoutUserGroup));
        when(users.getUserById(realm, "u1")).thenReturn(target);

        // Re-approving someone already in scout-user must be a no-op, not a
        // clobber: no setAttribute, no re-join (which would also re-fire the
        // grant event + welcome email). The adapter maps this to 409.
        assertThrows(IllegalStateException.class, () -> resource.applyApproval(
                new ScoutUsersResource.ApproveRequest("u1",
                        Map.of("allowed_facilities", List.of("WUSM")))));
        verify(target, never()).setAttribute(any(), any());
        verify(target, never()).joinGroup(any());
    }

    @Test
    void approve_with_one_invalid_attribute_writes_nothing() {
        UserModel target = mock(UserModel.class);
        when(users.getUserById(realm, "u1")).thenReturn(target);

        // One valid + one invalid value: validateAttributes runs over the whole
        // batch before applyApproval writes anything, so a single bad value must
        // leave nothing set and no group joined (the validate-before-write
        // invariant), independent of map iteration order.
        Map<String, List<String>> attrs = new HashMap<>();
        attrs.put("allowed_facilities", List.of("WUSM"));
        attrs.put("mask_phi_fields", List.of("bogus"));
        assertThrows(IllegalArgumentException.class, () -> resource.applyApproval(
                new ScoutUsersResource.ApproveRequest("u1", attrs)));
        verify(target, never()).setAttribute(any(), any());
        verify(target, never()).joinGroup(any());
    }

    // --- validate: free-text (pattern) dimensions ---------------------------
    // The shared profileConfig() uses options-validated attributes, which return
    // from validate() before its pattern branch. These hit the pattern path
    // directly -- the mechanism behind space-tolerant facility names like
    // "HOME CARE SERVICES" (policy/trino/main.rego widened its value pattern to
    // match). A free-text dimension is a pattern validation with no options.

    @Test
    void validate_accepts_pattern_matches_including_spaces() {
        UPAttribute facility = new UPAttribute("allowed_facilities");
        facility.setMultivalued(true);
        facility.setValidations(patternValidation("^[A-Za-z0-9 _-]+$"));

        // Must not throw: the pattern allows spaces, hyphens, and underscores.
        resource.validate(facility, List.of("HOME CARE SERVICES", "WUSM"));
    }

    @Test
    void validate_rejects_pattern_mismatch() {
        UPAttribute facility = new UPAttribute("allowed_facilities");
        facility.setMultivalued(true);
        facility.setValidations(patternValidation("^[A-Za-z0-9 _-]+$"));

        assertThrows(IllegalArgumentException.class,
                () -> resource.validate(facility, List.of("bad;value")));
    }

    // --- pending ------------------------------------------------------------

    @Test
    void pending_lists_terms_accepted_users_not_in_scout_user() {
        UserModel alice = candidate("alice", "ts", false); // accepted, not approved
        UserModel bob = candidate("bob", "ts", true); // accepted, already approved
        UserModel carol = candidate("carol", null, false); // never accepted terms
        when(users.searchForUserStream(eq(realm), anyMap())).thenAnswer(i -> Stream.of(alice, bob, carol));

        List<ScoutUsersResource.PendingUser> pending = resource.findPending();
        assertEquals(List.of("alice"),
                pending.stream().map(ScoutUsersResource.PendingUser::username).toList());
    }

    private UserModel candidate(String name, String termsAcceptedAt, boolean inScoutUser) {
        UserModel u = mock(UserModel.class);
        when(u.getUsername()).thenReturn(name);
        when(u.getFirstAttribute("scout_terms_accepted_at")).thenReturn(termsAcceptedAt);
        when(u.getGroupsStream()).thenAnswer(i ->
                inScoutUser ? Stream.of(scoutUserGroup) : Stream.<GroupModel>of());
        return u;
    }

    // --- auth decision ------------------------------------------------------

    @Test
    void is_scout_admin_only_for_group_members() {
        UserModel admin = mock(UserModel.class);
        when(admin.getGroupsStream()).thenAnswer(i -> Stream.of(scoutAdminGroup));
        UserModel plain = mock(UserModel.class);
        when(plain.getGroupsStream()).thenAnswer(i -> Stream.of(scoutUserGroup));

        assertTrue(resource.isScoutAdmin(admin));
        assertFalse(resource.isScoutAdmin(plain));
    }

    @Test
    void only_tokens_audienced_for_the_api_are_allowed() {
        // The launchpad client adds aud=scout-users-api; a token for another resource
        // (or with no audience) is rejected even if the user is a scout-admin.
        assertTrue(resource.hasRequiredAudience(new String[] {"scout-users-api"}));
        assertTrue(resource.hasRequiredAudience(new String[] {"account", "scout-users-api"}));
        assertFalse(resource.hasRequiredAudience(new String[] {"trino"}));
        assertFalse(resource.hasRequiredAudience(null));
    }

    // --- edit attributes ----------------------------------------------------

    @Test
    void set_attributes_updates_without_changing_groups() {
        UserModel target = mock(UserModel.class);
        when(target.getUsername()).thenReturn("alice");
        when(users.getUserById(realm, "u1")).thenReturn(target);

        resource.setAttributes("u1", Map.of("allowed_facilities", List.of("BJH")));

        verify(target).setAttribute("allowed_facilities", List.of("BJH"));
        verify(target, never()).joinGroup(any());
        verify(target, never()).leaveGroup(any());
    }

    @Test
    void set_attributes_rejects_unknown_key_without_applying() {
        UserModel target = mock(UserModel.class);
        when(users.getUserById(realm, "u1")).thenReturn(target);

        assertThrows(IllegalArgumentException.class, () ->
                resource.setAttributes("u1", Map.of("not_an_authz_attr", List.of("x"))));
        verify(target, never()).setAttribute(any(), any());
    }

    // --- promote / demote ---------------------------------------------------

    @Test
    void promote_joins_scout_admin() {
        UserModel target = mock(UserModel.class);
        when(target.getUsername()).thenReturn("alice");
        when(users.getUserById(realm, "u1")).thenReturn(target);

        resource.promote("u1");

        verify(target).joinGroup(scoutAdminGroup);
    }

    @Test
    void demote_leaves_scout_admin_when_another_admin_remains() {
        UserModel target = adminUser();
        when(target.getUsername()).thenReturn("alice");
        when(users.getUserById(realm, "u1")).thenReturn(target);
        whenAdminCountIs(2);

        resource.demote("u1");

        verify(target).leaveGroup(scoutAdminGroup);
    }

    @Test
    void demote_last_admin_is_rejected() {
        UserModel target = adminUser();
        when(users.getUserById(realm, "u1")).thenReturn(target);
        whenAdminCountIs(1);

        assertThrows(IllegalStateException.class, () -> resource.demote("u1"));
        verify(target, never()).leaveGroup(any());
        // Pin the rejection to the admin-count query (paginated to 2). Without
        // this, Mockito's default empty stream also yields count 0 <= 1, so the
        // test would pass even if the count path were never run or its args drifted.
        verify(users).getGroupMembersStream(realm, scoutAdminGroup, 0, 2);
    }

    // --- offboard -----------------------------------------------------------

    @Test
    void offboard_admin_removes_both_groups() {
        UserModel target = adminUser();
        when(target.getUsername()).thenReturn("alice");
        when(users.getUserById(realm, "u1")).thenReturn(target);
        whenAdminCountIs(2);

        ScoutUsersResource.OffboardResult result = resource.offboard("actor", "u1");

        verify(target).leaveGroup(scoutUserGroup);
        verify(target).leaveGroup(scoutAdminGroup);
        assertTrue(result.wasAdmin());
    }

    @Test
    void offboard_plain_user_removes_only_scout_user() {
        UserModel target = mock(UserModel.class);
        when(target.getUsername()).thenReturn("bob");
        when(target.getGroupsStream()).thenAnswer(i -> Stream.of(scoutUserGroup));
        when(users.getUserById(realm, "u2")).thenReturn(target);

        ScoutUsersResource.OffboardResult result = resource.offboard("actor", "u2");

        verify(target).leaveGroup(scoutUserGroup);
        verify(target, never()).leaveGroup(scoutAdminGroup);
        assertFalse(result.wasAdmin());
    }

    @Test
    void offboard_self_is_rejected_before_any_lookup() {
        assertThrows(IllegalStateException.class, () -> resource.offboard("u1", "u1"));
        verify(users, never()).getUserById(any(), any());
    }

    @Test
    void offboard_last_admin_is_rejected() {
        UserModel target = adminUser();
        when(users.getUserById(realm, "u1")).thenReturn(target);
        whenAdminCountIs(1);

        assertThrows(IllegalStateException.class, () -> resource.offboard("actor", "u1"));
        verify(target, never()).leaveGroup(any());
        verify(users).getGroupMembersStream(realm, scoutAdminGroup, 0, 2);
    }

    // --- console projection -------------------------------------------------

    @Test
    void to_scout_user_derives_admin_status_and_hides_non_authz_attributes() {
        UserModel admin = mock(UserModel.class);
        when(admin.getId()).thenReturn("a1");
        when(admin.getUsername()).thenReturn("adminuser");
        when(admin.getGroupsStream()).thenAnswer(i -> Stream.of(scoutUserGroup, scoutAdminGroup));
        Map<String, List<String>> raw = new HashMap<>();
        raw.put("allowed_facilities", List.of("WUSM"));
        raw.put("scout_terms_accepted_at", List.of("123"));
        raw.put("scout_admin_approval_email_sent_at", List.of("456"));
        when(admin.getAttributes()).thenReturn(raw);

        ScoutUsersResource.ScoutUser su = resource.toScoutUser(admin);

        assertEquals("admin", su.status());
        assertTrue(su.isAdmin());
        assertEquals(Set.of("allowed_facilities"), su.attributes().keySet(),
                "only scoutAuthz attributes are exposed -- terms/email-sent must not leak");
    }

    @Test
    void to_scout_user_is_pending_when_terms_accepted_not_active() {
        UserModel u = mock(UserModel.class);
        when(u.getGroupsStream()).thenAnswer(i -> Stream.<GroupModel>of());
        when(u.getFirstAttribute("scout_terms_accepted_at")).thenReturn("ts");
        when(u.getAttributes()).thenReturn(Map.of());

        ScoutUsersResource.ScoutUser su = resource.toScoutUser(u);

        assertEquals("pending", su.status());
        assertFalse(su.isAdmin());
    }

    @Test
    void status_filter_active_includes_admins() {
        ScoutUsersResource.ScoutUser pending = scoutUser("pending", false);
        assertTrue(resource.statusMatches(pending, "pending"));
        ScoutUsersResource.ScoutUser active = scoutUser("active", false);
        assertFalse(resource.statusMatches(active, "pending"));
        assertTrue(resource.statusMatches(active, "active"));
        ScoutUsersResource.ScoutUser admin = scoutUser("admin", true);
        assertTrue(resource.statusMatches(admin, "active"), "active includes admins");
        assertTrue(resource.statusMatches(admin, "admins"));
        assertFalse(resource.statusMatches(active, "admins"));
        assertTrue(resource.statusMatches(pending, null), "no filter matches everything");
    }

    // --- helpers for the above ----------------------------------------------

    private UserModel adminUser() {
        UserModel u = mock(UserModel.class);
        when(u.getGroupsStream()).thenAnswer(i -> Stream.of(scoutUserGroup, scoutAdminGroup));
        return u;
    }

    private void whenAdminCountIs(int n) {
        when(users.getGroupMembersStream(realm, scoutAdminGroup, 0, 2))
                .thenAnswer(i -> Stream.generate(() -> mock(UserModel.class)).limit(n));
    }

    private ScoutUsersResource.ScoutUser scoutUser(String status, boolean isAdmin) {
        return new ScoutUsersResource.ScoutUser("id", "u", null, null, status, isAdmin, Map.of());
    }
}
