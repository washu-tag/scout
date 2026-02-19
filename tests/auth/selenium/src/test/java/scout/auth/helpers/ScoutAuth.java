package scout.auth.helpers;

import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;

import java.time.Duration;

/**
 * Scout sign-in flow helper.
 *
 * Navigates through OAuth2 Proxy → Keycloak login.
 * Requires that the identity-provider-redirector execution has been
 * disabled in the Keycloak browser flow (via KeycloakAdmin) so that
 * the login form is shown instead of auto-redirecting to the external IdP.
 */
public final class ScoutAuth {

    private ScoutAuth() {}

    /**
     * Perform the full Scout sign-in flow.
     *
     * After this method returns, the browser has a valid _oauth2_proxy cookie
     * and is on the final post-login page (either "Access Pending" 403 or
     * the actual service page).
     */
    public static void signInToScout(WebDriver driver, String targetUrl,
                                     String username, String password) {
        // Navigate to target — OAuth2 Proxy serves the sign-in page
        driver.get(targetUrl);
        WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(30));

        // Verify we're on the OAuth2 Proxy sign-in page
        WebElement heading = wait.until(ExpectedConditions.presenceOfElementLocated(By.tagName("h2")));
        String headingText = heading.getText().trim();
        if (!"Welcome to Scout".equals(headingText)) {
            throw new RuntimeException(
                    "Expected sign-in page with \"Welcome to Scout\", got \"" + headingText + "\"");
        }

        // Click the "Sign In" button to submit the form naturally
        WebElement submitButton = driver.findElement(By.cssSelector("button.btn"));
        submitButton.click();

        // Wait for Keycloak login form
        wait.until(ExpectedConditions.presenceOfElementLocated(By.id("username")));

        // Fill credentials and submit
        driver.findElement(By.id("username")).sendKeys(username);
        driver.findElement(By.id("password")).sendKeys(password);
        driver.findElement(By.id("kc-login")).click();

        // Wait for redirect chain to complete (URL host is no longer keycloak.*)
        // Note: can't use urlContains("keycloak") because the OAuth2 Proxy callback
        // URL includes "keycloak" in the iss query parameter.
        wait.until(ExpectedConditions.not(ExpectedConditions.urlMatches("^https://keycloak\\.")));
    }
}
