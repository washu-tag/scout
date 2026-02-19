package scout.auth;

import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import scout.auth.config.TestConfig;
import scout.auth.helpers.ScoutAuth;

import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Phase 1 authorization tests: verify unauthorized users get 403 Access Pending.
 */
@ExtendWith(TestLifecycle.class)
class AuthorizationTest {

    static Stream<Arguments> unauthorizedServices() {
        String hostname = TestConfig.hostname();
        return Stream.of(
                // Root URLs for all Scout services
                Arguments.of("Launchpad", "https://" + hostname + "/"),
                Arguments.of("Superset", "https://superset." + hostname + "/"),
                Arguments.of("JupyterHub", "https://jupyter." + hostname + "/"),
                Arguments.of("Grafana", "https://grafana." + hostname + "/"),
                Arguments.of("Temporal", "https://temporal." + hostname + "/"),
                Arguments.of("MinIO", "https://minio." + hostname + "/"),
                Arguments.of("Open WebUI", "https://chat." + hostname + "/"),
                Arguments.of("Playbooks", "https://playbooks." + hostname + "/"),
                Arguments.of("Nonexistent Service", "https://nonexistent." + hostname + "/"),
                // Deep service paths — verify inner pages are also blocked
                Arguments.of("Superset SQL Lab", "https://superset." + hostname + "/sqllab/"),
                Arguments.of("JupyterHub Spawn", "https://jupyter." + hostname + "/hub/spawn"),
                Arguments.of("Grafana Dashboards", "https://grafana." + hostname + "/dashboards"),
                Arguments.of("Temporal Workflows", "https://temporal." + hostname + "/namespaces/default/workflows"),
                Arguments.of("MinIO Browser", "https://minio." + hostname + "/browser/lake/hl7"),
                Arguments.of("Playbooks Notebook", "https://playbooks." + hostname + "/voila/render/cohort/Cohort.ipynb")
        );
    }

    @ParameterizedTest(name = "{0} returns 403 Access Pending")
    @MethodSource("unauthorizedServices")
    void unauthorizedUserGetsAccessPending(String name, String url,
                                           WebDriver driver, TestLifecycle.TestUser user) {
        ScoutAuth.signInToScout(driver, url, user.username(), user.password());

        // Verify "Access Pending" heading
        assertEquals("Access Pending", driver.findElement(By.tagName("h1")).getText().trim());

        // Verify title contains 403
        assertTrue(driver.getTitle().contains("403"),
                "Expected page title to contain '403', got: " + driver.getTitle());

        // Verify return-to-scout link is displayed
        assertTrue(driver.findElement(By.id("return-to-scout")).isDisplayed());
    }
}
