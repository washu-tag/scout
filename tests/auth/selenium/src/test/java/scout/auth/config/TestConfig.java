package scout.auth.config;

import io.github.cdimascio.dotenv.Dotenv;

/**
 * Loads configuration from .env file and environment variables.
 *
 * Environment variables take precedence over .env values (dotenv-java default).
 */
public final class TestConfig {

    private static final Dotenv dotenv = Dotenv.configure()
            .ignoreIfMissing()
            .load();

    private TestConfig() {}

    public static String hostname() {
        return require("SCOUT_HOSTNAME");
    }

    public static String keycloakAdminUser() {
        return get("KEYCLOAK_ADMIN_USER", "admin");
    }

    public static String keycloakAdminPassword() {
        return require("KEYCLOAK_ADMIN_PASSWORD");
    }

    public static String testUserPassword() {
        return require("TEST_USER_PASSWORD");
    }

    public static String unauthorizedUserUsername() {
        return get("UNAUTHORIZED_USER_USERNAME", "scout-unauthorized-test-user");
    }

    public static String unauthorizedUserEmail() {
        return get("UNAUTHORIZED_USER_EMAIL", unauthorizedUserUsername() + "@example.com");
    }

    public static String unauthorizedUserFirstName() {
        return get("UNAUTHORIZED_USER_FIRST_NAME", "Unauthorized");
    }

    public static String unauthorizedUserLastName() {
        return get("UNAUTHORIZED_USER_LAST_NAME", "TestUser");
    }

    public static String authorizedUserUsername() {
        return get("AUTHORIZED_USER_USERNAME", "scout-authorized-test-user");
    }

    public static String authorizedUserEmail() {
        return get("AUTHORIZED_USER_EMAIL", authorizedUserUsername() + "@example.com");
    }

    public static String authorizedUserFirstName() {
        return get("AUTHORIZED_USER_FIRST_NAME", "Authorized");
    }

    public static String authorizedUserLastName() {
        return get("AUTHORIZED_USER_LAST_NAME", "TestUser");
    }

    private static String get(String key, String defaultValue) {
        String value = dotenv.get(key);
        return value != null ? value : defaultValue;
    }

    private static String require(String key) {
        String value = dotenv.get(key);
        if (value == null || value.isBlank()) {
            throw new IllegalStateException(key + " is not set");
        }
        return value;
    }
}
