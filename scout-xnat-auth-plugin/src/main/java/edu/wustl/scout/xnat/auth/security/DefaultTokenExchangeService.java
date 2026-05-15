package edu.wustl.scout.xnat.auth.security;

import com.fasterxml.jackson.databind.JsonNode;
import com.google.common.cache.Cache;
import com.google.common.cache.CacheBuilder;
import edu.wustl.scout.xnat.auth.ScoutAuthProperties;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Base64;
import java.util.concurrent.TimeUnit;

/**
 * Default Keycloak-backed implementation of {@link TokenExchangeService}.
 * Authenticates as the {@code xnat} client via HTTP Basic.
 *
 * <p>Caches successful exchanges in a Guava {@link Cache} keyed by SHA-256
 * of the incoming JWT (avoids retaining raw tokens longer than necessary).
 * TTL uses each exchanged token's {@code expires_in} minus a small safety
 * margin. The cache is purely an in-process optimization; restart-resilience
 * is not a goal.
 */
@Slf4j
public class DefaultTokenExchangeService implements TokenExchangeService {

    private static final String GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange";
    private static final String SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token";
    private static final long SAFETY_MARGIN_SECONDS = 30;

    private final ScoutAuthProperties properties;
    private final RestTemplate restTemplate;
    private final Cache<String, CachedExchange> cache;

    public DefaultTokenExchangeService(final ScoutAuthProperties properties) {
        this(properties, new RestTemplate(), defaultCache());
    }

    /** Visible for testing. */
    DefaultTokenExchangeService(final ScoutAuthProperties properties,
                                final RestTemplate restTemplate,
                                final Cache<String, CachedExchange> cache) {
        this.properties = properties;
        this.restTemplate = restTemplate;
        this.cache = cache;
    }

    @Override
    public String exchange(final String subjectJwt) throws TokenExchangeException {
        if (StringUtils.isBlank(subjectJwt)) {
            throw new TokenExchangeException("subject JWT must not be blank");
        }
        final String cacheKey = sha256Hex(subjectJwt);
        final CachedExchange cached = cache.getIfPresent(cacheKey);
        if (cached != null && cached.isStillValid(System.currentTimeMillis())) {
            return cached.token;
        }
        final CachedExchange fresh = doExchange(subjectJwt);
        cache.put(cacheKey, fresh);
        return fresh.token;
    }

    private CachedExchange doExchange(final String subjectJwt) throws TokenExchangeException {
        if (StringUtils.isBlank(properties.getTokenUri())) {
            throw new TokenExchangeException("scout.keycloak.token_uri is not configured");
        }
        if (StringUtils.isBlank(properties.getClientId()) || StringUtils.isBlank(properties.getClientSecret())) {
            throw new TokenExchangeException("scout.keycloak.client_id/client_secret are not configured");
        }

        final HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_FORM_URLENCODED);
        headers.setAccept(java.util.Collections.singletonList(MediaType.APPLICATION_JSON));
        headers.set(HttpHeaders.AUTHORIZATION, basicAuth(properties.getClientId(), properties.getClientSecret()));

        final MultiValueMap<String, String> form = new LinkedMultiValueMap<>();
        form.add("grant_type", GRANT_TYPE);
        form.add("subject_token", subjectJwt);
        form.add("subject_token_type", SUBJECT_TOKEN_TYPE);
        // No `audience` param: in Keycloak STX V2, the requester is implicitly
        // the audience. Passing `audience=<our own client_id>` produces a
        // 400 "Requested audience not available" because the requester isn't a
        // valid *additional* audience in the subject token from its own
        // perspective.

        final HttpEntity<MultiValueMap<String, String>> request = new HttpEntity<>(form, headers);

        try {
            final ResponseEntity<JsonNode> response = restTemplate.exchange(
                    properties.getTokenUri(), HttpMethod.POST, request, JsonNode.class);
            final JsonNode body = response.getBody();
            if (body == null || !body.hasNonNull("access_token")) {
                throw new TokenExchangeException("token exchange returned no access_token");
            }
            final String token = body.get("access_token").asText();
            final long expiresInSeconds = body.hasNonNull("expires_in") ? body.get("expires_in").asLong() : 60L;
            final long expiresAt = System.currentTimeMillis() + (expiresInSeconds - SAFETY_MARGIN_SECONDS) * 1000L;
            return new CachedExchange(token, expiresAt);
        } catch (RestClientException e) {
            log.warn("token exchange call to {} failed: {}", properties.getTokenUri(), e.getMessage());
            throw new TokenExchangeException("token exchange request failed: " + e.getMessage(), e);
        }
    }

    static String basicAuth(final String user, final String password) {
        final String creds = user + ":" + password;
        return "Basic " + Base64.getEncoder().encodeToString(creds.getBytes(StandardCharsets.UTF_8));
    }

    static String sha256Hex(final String input) {
        try {
            final MessageDigest digest = MessageDigest.getInstance("SHA-256");
            final byte[] bytes = digest.digest(input.getBytes(StandardCharsets.UTF_8));
            final StringBuilder hex = new StringBuilder(bytes.length * 2);
            for (byte b : bytes) {
                hex.append(String.format("%02x", b));
            }
            return hex.toString();
        } catch (NoSuchAlgorithmException e) {
            // SHA-256 is mandated by the JDK; this should never happen.
            throw new IllegalStateException("SHA-256 not available", e);
        }
    }

    private static Cache<String, CachedExchange> defaultCache() {
        // Bound the cache to keep memory predictable. Per-entry TTL is
        // enforced via CachedExchange.isStillValid; expireAfterWrite is a
        // backstop in case an entry's nominal TTL was somehow miscalculated.
        return CacheBuilder.newBuilder()
                .maximumSize(1024)
                .expireAfterWrite(15, TimeUnit.MINUTES)
                .build();
    }

    static final class CachedExchange {
        final String token;
        final long expiresAtMillis;

        CachedExchange(final String token, final long expiresAtMillis) {
            this.token = token;
            this.expiresAtMillis = expiresAtMillis;
        }

        boolean isStillValid(final long nowMillis) {
            return nowMillis < expiresAtMillis;
        }
    }
}
