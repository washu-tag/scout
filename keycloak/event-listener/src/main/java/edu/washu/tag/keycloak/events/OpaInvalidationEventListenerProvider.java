package edu.washu.tag.keycloak.events;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.ExecutorService;
import org.jboss.logging.Logger;
import org.keycloak.events.Event;
import org.keycloak.events.EventListenerProvider;
import org.keycloak.events.admin.AdminEvent;
import org.keycloak.events.admin.OperationType;
import org.keycloak.events.admin.ResourceType;
import org.keycloak.executors.ExecutorsProvider;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserModel;

/**
 * Event listener that pushes per-user cache-invalidation timestamps to OPA
 * when a user's Keycloak state changes (attributes updated, enable/disable
 * flipped, account deleted). The Trino RBAC policy includes the timestamp
 * in the URL it sends to Keycloak's admin API via {@code http.send} — when
 * the timestamp changes, OPA's cache key for that user changes too, forcing
 * a fresh fetch on the next policy decision. Without this push the cache
 * holds for {@code force_cache_duration_seconds} (60s today), which means
 * a disabled user retains data access for up to that window.
 *
 * <p>Scope: AdminEvents on {@link ResourceType#USER} with operations UPDATE
 * or DELETE. CREATE is skipped because a freshly-created user has no
 * cached state to invalidate. User-level events ({@link Event}) are
 * ignored — RBAC-relevant changes happen via the admin API.
 *
 * <p>OPA endpoint: {@code <KC_OPA_INVALIDATION_BASE_URLS>/<username>}.
 * Multiple URLs can be supplied comma-separated, one per OPA replica, so
 * every replica's in-memory data tree gets the update. A single ClusterIP
 * URL would load-balance to one replica and leave the others stale; until
 * we move to OPA bundles for distribution, explicit per-replica push is
 * the simplest correct option.
 *
 * <p>Dispatched to {@link ExecutorsProvider}'s managed thread pool so the
 * HTTP roundtrips don't land on the admin request path.
 */
public class OpaInvalidationEventListenerProvider implements EventListenerProvider {

    private static final Logger log = Logger.getLogger(OpaInvalidationEventListenerProvider.class);
    private static final String EXECUTOR_NAME = "scout-opa-invalidation";
    // OPA's Data API path that the Trino RBAC policy reads in
    // `data.keycloak_invalidations[<user>]`. PUT semantics: replace the
    // value at the path with the JSON body of the request.
    private static final String OPA_DATA_PATH_PREFIX = "/v1/data/keycloak_invalidations/";

    private final KeycloakSession session;
    private final List<String> opaBaseUrls;
    private final HttpClient httpClient;

    /**
     * Constructs a provider bound to a single Keycloak session.
     *
     * @param session     active Keycloak session, used to look up usernames for UPDATE events
     * @param opaBaseUrls list of OPA base URLs (one per replica). Empty list
     *                    disables the listener — useful in environments without OPA
     *                    (e.g., the staging cluster, CI).
     * @param httpClient  shared client built by the factory so each event reuses connections
     */
    public OpaInvalidationEventListenerProvider(KeycloakSession session, List<String> opaBaseUrls,
                                                HttpClient httpClient) {
        this.session = session;
        this.opaBaseUrls = opaBaseUrls;
        this.httpClient = httpClient;
    }

    @Override
    public void onEvent(Event event) {
        // User-level events (login, logout, password change) don't change
        // the RBAC-relevant attributes that OPA caches. Ignored.
    }

    @Override
    public void onEvent(AdminEvent event, boolean includeRepresentation) {
        if (opaBaseUrls.isEmpty()) {
            return;
        }
        if (event.getResourceType() != ResourceType.USER) {
            return;
        }
        OperationType op = event.getOperationType();
        if (op != OperationType.UPDATE && op != OperationType.DELETE) {
            return;
        }

        String username = resolveUsername(event);
        if (username == null) {
            log.debugf("Could not resolve username for admin event %s on %s; skipping OPA invalidation",
                    event.getOperationType(), event.getResourcePath());
            return;
        }

        long timestamp = System.currentTimeMillis();
        ExecutorService executor = session.getProvider(ExecutorsProvider.class).getExecutor(EXECUTOR_NAME);
        executor.execute(() -> pushInvalidation(username, timestamp));
    }

    @Override
    public void close() {
    }

    /**
     * Resolve the username for an admin event. For UPDATE we have the user
     * row to read; for DELETE the row is gone but Keycloak sets the
     * username in {@code details.username} on user-deletion events.
     */
    private String resolveUsername(AdminEvent event) {
        // resourcePath shape: "users/{userId}" or "users/{userId}/disable-credential-types" etc.
        String path = event.getResourcePath();
        if (path == null || !path.startsWith("users/")) {
            return null;
        }
        String userId = path.substring("users/".length()).split("/", 2)[0];

        if (event.getOperationType() == OperationType.DELETE) {
            // Row is gone — fall back to the event's details map.
            if (event.getDetails() != null) {
                String fromDetails = event.getDetails().get("username");
                if (fromDetails != null) {
                    return fromDetails;
                }
            }
            log.warnf("DELETE admin event for user %s has no username in details; invalidation key unknown", userId);
            return null;
        }

        RealmModel realm = session.realms().getRealm(event.getRealmId());
        if (realm == null) {
            return null;
        }
        UserModel user = session.users().getUserById(realm, userId);
        return user != null ? user.getUsername() : null;
    }

    private void pushInvalidation(String username, long timestamp) {
        // OPA's Data API PUT writes a JSON value at the path. We store a
        // bare number — the policy reads it as a cache-busting suffix on
        // the http.send URL, so any change to the value forces a fresh
        // fetch on the next decision. No need for a richer payload.
        String body = Long.toString(timestamp);
        String pathSuffix = OPA_DATA_PATH_PREFIX + URLEncoder.encode(username, StandardCharsets.UTF_8);

        for (String baseUrl : opaBaseUrls) {
            String url = stripTrailingSlash(baseUrl) + pathSuffix;
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(url))
                    .timeout(Duration.ofSeconds(3))
                    .header("Content-Type", "application/json")
                    .PUT(HttpRequest.BodyPublishers.ofString(body))
                    .build();
            try {
                HttpResponse<String> resp = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
                if (resp.statusCode() >= 200 && resp.statusCode() < 300) {
                    log.infof("OPA invalidated user %s at %s (ts=%d)", username, url, timestamp);
                } else {
                    log.warnf("OPA invalidation for %s at %s returned %d: %s",
                            username, url, resp.statusCode(), resp.body());
                }
            } catch (Exception e) {
                // Don't rethrow — Keycloak admin operations shouldn't fail
                // because OPA is briefly unreachable. The 60s cache TTL
                // bounds the window where stale attrs are served.
                log.errorf("OPA invalidation request failed for %s at %s: %s",
                        username, url, e.getMessage());
            }
        }
    }

    private static String stripTrailingSlash(String s) {
        return s.endsWith("/") ? s.substring(0, s.length() - 1) : s;
    }
}
