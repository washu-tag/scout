package edu.washu.tag.keycloak.events;

import java.net.http.HttpClient;
import java.time.Duration;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.stream.Collectors;
import org.jboss.logging.Logger;
import org.keycloak.Config;
import org.keycloak.events.EventListenerProviderFactory;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;

/**
 * Factory for {@link OpaInvalidationEventListenerProvider}. Builds a shared
 * {@link HttpClient} so each event doesn't pay TLS/handshake setup, and
 * parses the comma-separated list of OPA base URLs from
 * {@code KC_OPA_INVALIDATION_BASE_URLS} once at init.
 */
public class OpaInvalidationEventListenerProviderFactory implements EventListenerProviderFactory {

    public static final String ID = "opa-invalidation";
    // Comma-separated list of OPA base URLs, one per replica. Reading from
    // env (not realm config) keeps the listener stateless across realms and
    // matches the existing UserApprovalEmail listener's env-var pattern.
    static final String ENV_OPA_BASE_URLS = "KC_OPA_INVALIDATION_BASE_URLS";

    private static final Logger log = Logger.getLogger(OpaInvalidationEventListenerProviderFactory.class);

    private List<String> opaBaseUrls = Collections.emptyList();
    private HttpClient httpClient;

    @Override
    public OpaInvalidationEventListenerProvider create(KeycloakSession session) {
        return new OpaInvalidationEventListenerProvider(session, opaBaseUrls, httpClient);
    }

    @Override
    public void init(Config.Scope config) {
        String raw = System.getenv(ENV_OPA_BASE_URLS);
        if (raw == null || raw.isBlank()) {
            log.infof("%s is unset — OPA invalidation listener will no-op.", ENV_OPA_BASE_URLS);
            opaBaseUrls = Collections.emptyList();
        } else {
            opaBaseUrls = Arrays.stream(raw.split(","))
                    .map(String::trim)
                    .filter(s -> !s.isEmpty())
                    .collect(Collectors.toUnmodifiableList());
            log.infof("OPA invalidation targets: %s", opaBaseUrls);
        }

        httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(2))
                .build();
    }

    @Override
    public void postInit(KeycloakSessionFactory factory) {
    }

    @Override
    public void close() {
    }

    @Override
    public String getId() {
        return ID;
    }
}
