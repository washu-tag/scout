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
}
