package edu.washu.tag.keycloak.events;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.keycloak.events.admin.AdminEvent;
import org.keycloak.events.admin.OperationType;
import org.keycloak.events.admin.ResourceType;

/**
 * Unit tests for the pure helpers behind the admin approval email: siteLabel
 * stamps the deployment (bare host of KC_SCOUT_URL) onto the subject/body so
 * admins running multiple Scout environments can tell which one a request is
 * for, and approvalUrl builds the deep-link to the in-Keycloak approval page.
 * isScoutUserGroupMembershipEvent decides which admin events route to the
 * enabled/disabled emails. All are pure (no Keycloak session), so tested directly.
 */
class UserApprovalEmailEventListenerProviderTest {

    @Test
    void siteLabel_uses_bare_host_as_is() {
        assertEquals(Optional.of("scout.dev04.example.edu"),
                UserApprovalEmailEventListenerProvider.siteLabel("scout.dev04.example.edu"));
    }

    @Test
    void siteLabel_strips_scheme_and_path() {
        assertEquals(Optional.of("scout.example.edu"),
                UserApprovalEmailEventListenerProvider.siteLabel("https://scout.example.edu"));
        assertEquals(Optional.of("scout.example.edu"),
                UserApprovalEmailEventListenerProvider.siteLabel("http://scout.example.edu/realms/scout"));
    }

    @Test
    void siteLabel_empty_when_unset_or_blank() {
        assertEquals(Optional.empty(), UserApprovalEmailEventListenerProvider.siteLabel(null));
        assertEquals(Optional.empty(), UserApprovalEmailEventListenerProvider.siteLabel(""));
        assertEquals(Optional.empty(), UserApprovalEmailEventListenerProvider.siteLabel("https://"));
    }

    @Test
    void approvalUrl_builds_launchpad_deep_link() {
        assertEquals("https://scout.example.edu/admin/users?user=u-123",
                UserApprovalEmailEventListenerProvider.approvalUrl("https://scout.example.edu", "u-123"));
    }

    @Test
    void approvalUrl_trims_trailing_slash_on_base() {
        assertEquals("https://scout.example.edu/admin/users?user=u-123",
                UserApprovalEmailEventListenerProvider.approvalUrl("https://scout.example.edu/", "u-123"));
    }

    // --- group-membership routing (drives the enabled/disabled emails) ------
    // The scout-users SPI fires GROUP_MEMBERSHIP CREATE/DELETE admin events with
    // the group representation in the body; the listener routes only the
    // scout-user ones to the enabled/disabled email. These pin which events route.

    private AdminEvent groupEvent(OperationType op, ResourceType type, String representation) {
        AdminEvent event = new AdminEvent();
        event.setOperationType(op);
        event.setResourceType(type);
        event.setRealmId("scout");
        event.setRepresentation(representation);
        return event;
    }

    @Test
    void routes_scout_user_add_and_remove() {
        assertTrue(UserApprovalEmailEventListenerProvider.isScoutUserGroupMembershipEvent(
                groupEvent(OperationType.CREATE, ResourceType.GROUP_MEMBERSHIP, "{\"name\":\"scout-user\"}")));
        assertTrue(UserApprovalEmailEventListenerProvider.isScoutUserGroupMembershipEvent(
                groupEvent(OperationType.DELETE, ResourceType.GROUP_MEMBERSHIP, "{\"name\":\"scout-user\"}")));
    }

    @Test
    void ignores_other_groups_and_operations() {
        // A different group in the representation -> not routed.
        assertFalse(UserApprovalEmailEventListenerProvider.isScoutUserGroupMembershipEvent(
                groupEvent(OperationType.CREATE, ResourceType.GROUP_MEMBERSHIP, "{\"name\":\"scout-admin\"}")));
        // UPDATE is neither an add nor a remove.
        assertFalse(UserApprovalEmailEventListenerProvider.isScoutUserGroupMembershipEvent(
                groupEvent(OperationType.UPDATE, ResourceType.GROUP_MEMBERSHIP, "{\"name\":\"scout-user\"}")));
        // Same op on a non-group-membership resource -> not routed.
        assertFalse(UserApprovalEmailEventListenerProvider.isScoutUserGroupMembershipEvent(
                groupEvent(OperationType.CREATE, ResourceType.USER, "{\"name\":\"scout-user\"}")));
    }

    @Test
    void ignores_event_with_no_representation() {
        assertFalse(UserApprovalEmailEventListenerProvider.isScoutUserGroupMembershipEvent(
                groupEvent(OperationType.CREATE, ResourceType.GROUP_MEMBERSHIP, null)));
    }
}
