package scout.auth.helpers;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import scout.auth.config.TestConfig;

import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.security.cert.X509Certificate;
import java.util.List;

/**
 * Keycloak Admin API helper.
 *
 * Uses java.net.http.HttpClient to manage test users in the Scout realm
 * via the Keycloak Admin REST API.
 */
public class KeycloakAdmin {

    private static final Gson GSON = new Gson();

    private final String baseUrl;
    private final String adminUser;
    private final String adminPassword;
    private final HttpClient httpClient;
    private String accessToken;

    public KeycloakAdmin() {
        String hostname = TestConfig.hostname();
        this.baseUrl = "https://keycloak." + hostname;
        this.adminUser = TestConfig.keycloakAdminUser();
        this.adminPassword = TestConfig.keycloakAdminPassword();
        this.httpClient = HttpClient.newBuilder()
                .sslContext(trustAllSSLContext())
                .build();
    }

    /** Obtain an admin access token from the master realm. */
    public void authenticate() throws Exception {
        String url = baseUrl + "/realms/master/protocol/openid-connect/token";
        String body = "grant_type=password"
                + "&client_id=admin-cli"
                + "&username=" + URLEncoder.encode(adminUser, StandardCharsets.UTF_8)
                + "&password=" + URLEncoder.encode(adminPassword, StandardCharsets.UTF_8);

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(url))
                .header("Content-Type", "application/x-www-form-urlencoded")
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();

        HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200) {
            throw new RuntimeException("Keycloak admin auth failed (" + response.statusCode() + "): " + response.body());
        }

        JsonObject json = GSON.fromJson(response.body(), JsonObject.class);
        this.accessToken = json.get("access_token").getAsString();
    }

    /** Create a user in the Scout realm. Returns the new user's ID. */
    public String createUser(UserConfig config) throws Exception {
        ensureAuthenticated();

        String url = baseUrl + "/admin/realms/scout/users";
        JsonObject userJson = new JsonObject();
        userJson.addProperty("username", config.username());
        userJson.addProperty("email", config.email());
        userJson.addProperty("firstName", config.firstName());
        userJson.addProperty("lastName", config.lastName());
        userJson.addProperty("enabled", true);
        userJson.addProperty("emailVerified", true);

        JsonObject credential = new JsonObject();
        credential.addProperty("type", "password");
        credential.addProperty("value", config.password());
        credential.addProperty("temporary", false);
        JsonArray credentials = new JsonArray();
        credentials.add(credential);
        userJson.add("credentials", credentials);

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(url))
                .header("Authorization", "Bearer " + accessToken)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(GSON.toJson(userJson)))
                .build();

        HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());

        if (response.statusCode() == 409) {
            System.out.println("User \"" + config.username() + "\" already exists, reusing.");
            return getUserByUsername(config.username());
        }

        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new RuntimeException("Failed to create user (" + response.statusCode() + "): " + response.body());
        }

        // Extract user ID from Location header
        String location = response.headers().firstValue("location").orElse(null);
        String userId;
        if (location != null) {
            String[] parts = location.split("/");
            userId = parts[parts.length - 1];
        } else {
            userId = getUserByUsername(config.username());
        }

        System.out.println("Created user \"" + config.username() + "\" (" + userId + ")");

        // Assign groups if specified
        if (config.groups() != null) {
            for (String groupName : config.groups()) {
                String groupId = getGroupByName(groupName);
                addUserToGroup(userId, groupId);
            }
        }

        return userId;
    }

    /** Look up a user by exact username. Returns the user ID. */
    public String getUserByUsername(String username) throws Exception {
        ensureAuthenticated();

        String url = baseUrl + "/admin/realms/scout/users?username="
                + URLEncoder.encode(username, StandardCharsets.UTF_8) + "&exact=true";

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(url))
                .header("Authorization", "Bearer " + accessToken)
                .GET()
                .build();

        HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200) {
            throw new RuntimeException("Failed to look up user (" + response.statusCode() + "): " + response.body());
        }

        JsonArray users = GSON.fromJson(response.body(), JsonArray.class);
        if (users.isEmpty()) {
            throw new RuntimeException("User \"" + username + "\" not found");
        }

        return users.get(0).getAsJsonObject().get("id").getAsString();
    }

    /** Delete a user by ID. */
    public void deleteUser(String userId) throws Exception {
        ensureAuthenticated();

        String url = baseUrl + "/admin/realms/scout/users/" + userId;
        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(url))
                .header("Authorization", "Bearer " + accessToken)
                .DELETE()
                .build();

        HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());

        if (response.statusCode() == 404) {
            System.out.println("User " + userId + " already deleted.");
            return;
        }

        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new RuntimeException("Failed to delete user (" + response.statusCode() + "): " + response.body());
        }

        System.out.println("Deleted user " + userId);
    }

    /** Delete a user by username (idempotent — no error if user doesn't exist). */
    public void deleteUserByUsername(String username) throws Exception {
        ensureAuthenticated();

        try {
            String userId = getUserByUsername(username);
            deleteUser(userId);
        } catch (RuntimeException e) {
            if (e.getMessage().contains("not found")) {
                System.out.println("User \"" + username + "\" not found — nothing to delete.");
                return;
            }
            throw e;
        }
    }

    /** Look up a group by exact name. Returns the group ID. */
    public String getGroupByName(String name) throws Exception {
        ensureAuthenticated();

        String url = baseUrl + "/admin/realms/scout/groups?search="
                + URLEncoder.encode(name, StandardCharsets.UTF_8) + "&exact=true";

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(url))
                .header("Authorization", "Bearer " + accessToken)
                .GET()
                .build();

        HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200) {
            throw new RuntimeException("Failed to look up group (" + response.statusCode() + "): " + response.body());
        }

        JsonArray groups = GSON.fromJson(response.body(), JsonArray.class);
        if (groups.isEmpty()) {
            throw new RuntimeException("Group \"" + name + "\" not found");
        }

        return groups.get(0).getAsJsonObject().get("id").getAsString();
    }

    /** Add a user to a group. */
    public void addUserToGroup(String userId, String groupId) throws Exception {
        ensureAuthenticated();

        String url = baseUrl + "/admin/realms/scout/users/" + userId + "/groups/" + groupId;
        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(url))
                .header("Authorization", "Bearer " + accessToken)
                .PUT(HttpRequest.BodyPublishers.noBody())
                .build();

        HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new RuntimeException(
                    "Failed to add user " + userId + " to group " + groupId
                            + " (" + response.statusCode() + "): " + response.body());
        }

        System.out.println("Added user " + userId + " to group " + groupId);
    }

    /**
     * Disable the identity-provider-redirector execution in the browser
     * authentication flow so Keycloak shows the login form instead of
     * auto-redirecting to the default IdP (e.g. GitHub).
     */
    public void disableIdpRedirect() throws Exception {
        setIdpRedirectRequirement("DISABLED");
    }

    /**
     * Re-enable the identity-provider-redirector execution in the browser
     * authentication flow (restores auto-redirect to the default IdP).
     */
    public void enableIdpRedirect() throws Exception {
        setIdpRedirectRequirement("ALTERNATIVE");
    }

    private void setIdpRedirectRequirement(String requirement) throws Exception {
        ensureAuthenticated();

        // Get all executions in the browser flow
        String execUrl = baseUrl + "/admin/realms/scout/authentication/flows/browser/executions";
        HttpRequest getRequest = HttpRequest.newBuilder()
                .uri(URI.create(execUrl))
                .header("Authorization", "Bearer " + accessToken)
                .GET()
                .build();

        HttpResponse<String> getResponse = httpClient.send(getRequest, HttpResponse.BodyHandlers.ofString());
        if (getResponse.statusCode() != 200) {
            throw new RuntimeException("Failed to get browser flow executions ("
                    + getResponse.statusCode() + "): " + getResponse.body());
        }

        JsonArray executions = GSON.fromJson(getResponse.body(), JsonArray.class);
        JsonObject idpExecution = null;
        for (var element : executions) {
            JsonObject exec = element.getAsJsonObject();
            if (exec.has("providerId")
                    && "identity-provider-redirector".equals(exec.get("providerId").getAsString())) {
                idpExecution = exec;
                break;
            }
        }

        if (idpExecution == null) {
            throw new RuntimeException("identity-provider-redirector execution not found in browser flow");
        }

        if (requirement.equals(idpExecution.get("requirement").getAsString())) {
            System.out.println("identity-provider-redirector already " + requirement);
            return;
        }

        // Update the execution requirement
        idpExecution.addProperty("requirement", requirement);
        HttpRequest putRequest = HttpRequest.newBuilder()
                .uri(URI.create(execUrl))
                .header("Authorization", "Bearer " + accessToken)
                .header("Content-Type", "application/json")
                .PUT(HttpRequest.BodyPublishers.ofString(GSON.toJson(idpExecution)))
                .build();

        HttpResponse<String> putResponse = httpClient.send(putRequest, HttpResponse.BodyHandlers.ofString());
        if (putResponse.statusCode() < 200 || putResponse.statusCode() >= 300) {
            throw new RuntimeException("Failed to update identity-provider-redirector ("
                    + putResponse.statusCode() + "): " + putResponse.body());
        }

        System.out.println("Set identity-provider-redirector to " + requirement);
    }

    private void ensureAuthenticated() throws Exception {
        if (accessToken == null) {
            authenticate();
        }
    }

    /** Create an SSLContext that trusts all certificates (for self-signed certs). */
    private static SSLContext trustAllSSLContext() {
        try {
            TrustManager[] trustAll = new TrustManager[]{
                    new X509TrustManager() {
                        public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
                        public void checkClientTrusted(X509Certificate[] certs, String authType) {}
                        public void checkServerTrusted(X509Certificate[] certs, String authType) {}
                    }
            };
            SSLContext sslContext = SSLContext.getInstance("TLS");
            sslContext.init(null, trustAll, new SecureRandom());
            return sslContext;
        } catch (Exception e) {
            throw new RuntimeException("Failed to create trust-all SSLContext", e);
        }
    }

    /** Configuration for a test user to create in Keycloak. */
    public record UserConfig(
            String username,
            String password,
            String email,
            String firstName,
            String lastName,
            List<String> groups
    ) {
        public UserConfig(String username, String password, String email,
                          String firstName, String lastName) {
            this(username, password, email, firstName, lastName, null);
        }
    }
}
