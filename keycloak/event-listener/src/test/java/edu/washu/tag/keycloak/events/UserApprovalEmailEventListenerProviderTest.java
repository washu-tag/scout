package edu.washu.tag.keycloak.events;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.Optional;
import org.junit.jupiter.api.Test;

/**
 * Unit tests for the pure helpers behind the admin approval email: siteLabel
 * stamps the deployment (bare host of KC_SCOUT_URL) onto the subject/body so
 * admins running multiple Scout environments can tell which one a request is
 * for, and approvalUrl builds the deep-link to the in-Keycloak approval page.
 * Both are pure (no Keycloak session), so they're tested directly.
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
        assertEquals("https://scout.example.edu/admin/approvals?user=u-123",
                UserApprovalEmailEventListenerProvider.approvalUrl("https://scout.example.edu", "u-123"));
    }

    @Test
    void approvalUrl_trims_trailing_slash_on_base() {
        assertEquals("https://scout.example.edu/admin/approvals?user=u-123",
                UserApprovalEmailEventListenerProvider.approvalUrl("https://scout.example.edu/", "u-123"));
    }
}
