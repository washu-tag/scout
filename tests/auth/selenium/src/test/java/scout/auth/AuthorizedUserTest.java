package scout.auth;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;
import scout.auth.config.TestConfig;
import scout.auth.helpers.ScoutAuth;

import java.time.Duration;

import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Authorized user (scout-user group, non-admin) tests.
 * Verifies that admin-only services deny access to regular users.
 */
@ExtendWith(TestLifecycle.class)
class AuthorizedUserTest {

    private static final String HOSTNAME = TestConfig.hostname();

    @Test
    void launchpadHidesAdminLinks(WebDriver driver, @Authorized TestLifecycle.TestUser authorizedUser) {
        String url = "https://" + HOSTNAME + "/";
        ScoutAuth.signInToScout(driver, url, authorizedUser.username(), authorizedUser.password());

        // Wait for React app to render
        new WebDriverWait(driver, Duration.ofSeconds(15))
                .until(ExpectedConditions.presenceOfElementLocated(
                        By.xpath("//*[contains(., 'Core Services')]")));

        // Admin Tools section should NOT be visible for non-admin users
        assertTrue(driver.findElements(By.xpath("//*[contains(., 'Admin Tools')]")).isEmpty(),
                "Expected 'Admin Tools' to be hidden for non-admin users");
    }

    @Test
    void grafanaDeniesAccess(WebDriver driver, @Authorized TestLifecycle.TestUser authorizedUser) {
        String url = "https://grafana." + HOSTNAME + "/";
        ScoutAuth.signInToScout(driver, url, authorizedUser.username(), authorizedUser.password());

        new WebDriverWait(driver, Duration.ofSeconds(60))
                .until(ExpectedConditions.presenceOfElementLocated(
                        By.xpath("//*[contains(., 'Unauthorized')]")));
    }

    @Test
    void temporalDeniesAccess(WebDriver driver, @Authorized TestLifecycle.TestUser authorizedUser) {
        String url = "https://temporal." + HOSTNAME + "/";
        ScoutAuth.signInToScout(driver, url, authorizedUser.username(), authorizedUser.password());

        // Click "Continue to SSO" to complete Temporal's own auth flow
        new WebDriverWait(driver, Duration.ofSeconds(30))
                .until(ExpectedConditions.elementToBeClickable(
                        By.cssSelector("[data-testid='login-button']")))
                .click();

        // Temporal should show unauthorized for non-admin users
        new WebDriverWait(driver, Duration.ofSeconds(30))
                .until(ExpectedConditions.presenceOfElementLocated(
                        By.xpath("//*[contains(., 'Request unauthorized')]")));
    }

    @Test
    void minioDeniesAccess(WebDriver driver, @Authorized TestLifecycle.TestUser authorizedUser) {
        String url = "https://minio." + HOSTNAME + "/";
        ScoutAuth.signInToScout(driver, url, authorizedUser.username(), authorizedUser.password());

        // Click "Login with SSO" to complete MinIO's own auth flow
        new WebDriverWait(driver, Duration.ofSeconds(15))
                .until(ExpectedConditions.elementToBeClickable(
                        By.xpath("//button[contains(., 'Login with SSO')]")))
                .click();

        // Expect policy claim error for non-admin users
        new WebDriverWait(driver, Duration.ofSeconds(60))
                .until(ExpectedConditions.presenceOfElementLocated(
                        By.xpath("//*[contains(., 'Policy claim missing')]")));
    }
}
