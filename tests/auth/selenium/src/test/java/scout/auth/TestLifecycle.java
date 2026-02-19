package scout.auth;

import com.google.gson.Gson;
import com.google.gson.reflect.TypeToken;
import org.junit.jupiter.api.extension.*;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.chrome.ChromeDriver;
import org.openqa.selenium.chrome.ChromeOptions;
import scout.auth.config.TestConfig;
import scout.auth.helpers.KeycloakAdmin;
import scout.auth.helpers.KeycloakAdmin.UserConfig;

import java.io.IOException;
import java.lang.reflect.Type;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * JUnit 5 extension that manages test user lifecycle and WebDriver instances.
 *
 * - BeforeAll: Creates unauthorized + authorized test users in Keycloak once
 *   across the entire test suite (using the root ExtensionContext store),
 *   saves their IDs to .auth/test-users.json
 * - Teardown runs automatically when the root context closes (after all tests)
 * - BeforeEach: Creates a fresh headless Chrome WebDriver
 * - AfterEach: Quits the WebDriver
 *
 * Tests receive the WebDriver and test user via parameter resolution.
 * Parameter name "user" resolves to the unauthorized user;
 * parameter name "authorizedUser" resolves to the authorized user.
 */
public class TestLifecycle implements BeforeAllCallback,
        BeforeEachCallback, AfterEachCallback, ParameterResolver {

    private static final Gson GSON = new Gson();
    private static final Path AUTH_DIR = Path.of(".auth");
    private static final Path USERS_FILE = AUTH_DIR.resolve("test-users.json");

    // ExtensionContext.Store keys
    private static final ExtensionContext.Namespace NAMESPACE =
            ExtensionContext.Namespace.create(TestLifecycle.class);
    private static final String DRIVER_KEY = "webdriver";
    private static final String SETUP_STATE_KEY = "setupState";
    private static final String UNAUTHORIZED_USER_KEY = "unauthorizedUser";
    private static final String AUTHORIZED_USER_KEY = "authorizedUser";

    public record TestUser(String username, String password) {}

    private record TestUserRecord(String id, String username) {}

    /**
     * Teardown resource stored in the root context store.
     * JUnit calls close() after all tests finish.
     */
    private static class SetupState implements ExtensionContext.Store.CloseableResource {
        @Override
        public void close() throws Exception {
            System.out.println("\n--- Global Teardown ---");

            KeycloakAdmin keycloak = new KeycloakAdmin();

            try {
                System.out.println("Re-enabling IdP redirect in Keycloak browser flow...");
                keycloak.enableIdpRedirect();
            } catch (Exception e) {
                System.err.println("Warning: failed to re-enable IdP redirect: " + e.getMessage());
            }

            if (Files.exists(USERS_FILE)) {
                Type listType = new TypeToken<List<TestUserRecord>>() {}.getType();
                List<TestUserRecord> records = GSON.fromJson(Files.readString(USERS_FILE), listType);

                for (TestUserRecord record : records) {
                    try {
                        System.out.println("Deleting test user \"" + record.username() + "\" (" + record.id() + ")");
                        keycloak.deleteUser(record.id());
                    } catch (Exception e) {
                        System.err.println("Warning: failed to delete user \"" + record.username() + "\": " + e.getMessage());
                    }
                }
            } else {
                // Fallback: delete by username
                for (String username : List.of(
                        TestConfig.unauthorizedUserUsername(),
                        TestConfig.authorizedUserUsername())) {
                    try {
                        System.out.println("No saved user ID — deleting by username: " + username);
                        keycloak.deleteUserByUsername(username);
                    } catch (Exception e) {
                        System.err.println("Warning: failed to delete user \"" + username + "\": " + e.getMessage());
                    }
                }
            }

            // Clean up .auth directory
            if (Files.exists(AUTH_DIR)) {
                deleteDirectory(AUTH_DIR);
                System.out.println("Cleaned up .auth/ directory");
            }

            System.out.println("--- Teardown Complete ---\n");
        }

        private static void deleteDirectory(Path dir) throws IOException {
            if (Files.isDirectory(dir)) {
                try (var entries = Files.list(dir)) {
                    for (Path entry : entries.toList()) {
                        deleteDirectory(entry);
                    }
                }
            }
            Files.deleteIfExists(dir);
        }
    }

    @Override
    public void beforeAll(ExtensionContext context) throws Exception {
        // Use the root context store so setup/teardown runs exactly once
        ExtensionContext rootContext = context.getRoot();
        ExtensionContext.Store rootStore = rootContext.getStore(NAMESPACE);

        // If already set up, skip
        if (rootStore.get(SETUP_STATE_KEY) != null) {
            return;
        }

        System.out.println("\n--- Global Setup ---");

        Files.createDirectories(AUTH_DIR);

        KeycloakAdmin keycloak = new KeycloakAdmin();

        System.out.println("Disabling IdP redirect in Keycloak browser flow...");
        keycloak.disableIdpRedirect();

        List<TestUserRecord> users = new ArrayList<>();
        String password = TestConfig.testUserPassword();

        // Unauthorized user (no group membership)
        String unauthorizedId = keycloak.createUser(new UserConfig(
                TestConfig.unauthorizedUserUsername(),
                password,
                TestConfig.unauthorizedUserEmail(),
                TestConfig.unauthorizedUserFirstName(),
                TestConfig.unauthorizedUserLastName()
        ));
        users.add(new TestUserRecord(unauthorizedId, TestConfig.unauthorizedUserUsername()));

        // Authorized user (scout-user group)
        String authorizedId = keycloak.createUser(new UserConfig(
                TestConfig.authorizedUserUsername(),
                password,
                TestConfig.authorizedUserEmail(),
                TestConfig.authorizedUserFirstName(),
                TestConfig.authorizedUserLastName(),
                List.of("scout-user")
        ));
        users.add(new TestUserRecord(authorizedId, TestConfig.authorizedUserUsername()));

        // Save user records for teardown
        Files.writeString(USERS_FILE, GSON.toJson(users));
        System.out.println("Saved " + users.size() + " user records to " + USERS_FILE);

        // Store users and teardown resource in root store
        rootStore.put(UNAUTHORIZED_USER_KEY,
                new TestUser(TestConfig.unauthorizedUserUsername(), password));
        rootStore.put(AUTHORIZED_USER_KEY,
                new TestUser(TestConfig.authorizedUserUsername(), password));

        // Store SetupState — its close() handles teardown when the root context closes
        rootStore.put(SETUP_STATE_KEY, new SetupState());

        System.out.println("--- Setup Complete ---\n");
    }

    @Override
    public void beforeEach(ExtensionContext context) {
        ChromeOptions options = new ChromeOptions();
        options.addArguments("--headless=new");
        options.addArguments("--no-sandbox");
        options.addArguments("--disable-dev-shm-usage");
        options.addArguments("--disable-gpu");
        options.addArguments("--ignore-certificate-errors");
        options.addArguments("--window-size=1920,1080");

        WebDriver driver = new ChromeDriver(options);
        context.getStore(NAMESPACE).put(DRIVER_KEY, driver);
    }

    @Override
    public void afterEach(ExtensionContext context) {
        WebDriver driver = context.getStore(NAMESPACE).get(DRIVER_KEY, WebDriver.class);
        if (driver != null) {
            driver.quit();
        }
    }

    @Override
    public boolean supportsParameter(ParameterContext parameterContext, ExtensionContext extensionContext) {
        Class<?> type = parameterContext.getParameter().getType();
        return type == WebDriver.class || type == TestUser.class;
    }

    @Override
    public Object resolveParameter(ParameterContext parameterContext, ExtensionContext extensionContext) {
        Class<?> type = parameterContext.getParameter().getType();
        if (type == WebDriver.class) {
            return extensionContext.getStore(NAMESPACE).get(DRIVER_KEY, WebDriver.class);
        }
        if (type == TestUser.class) {
            ExtensionContext.Store rootStore = extensionContext.getRoot().getStore(NAMESPACE);
            if (parameterContext.isAnnotated(Authorized.class)) {
                return rootStore.get(AUTHORIZED_USER_KEY, TestUser.class);
            }
            return rootStore.get(UNAUTHORIZED_USER_KEY, TestUser.class);
        }
        throw new ParameterResolutionException("Unsupported parameter type: " + type);
    }
}
