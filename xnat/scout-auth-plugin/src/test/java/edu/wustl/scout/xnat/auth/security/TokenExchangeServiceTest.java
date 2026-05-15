package edu.wustl.scout.xnat.auth.security;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.google.common.cache.Cache;
import com.google.common.cache.CacheBuilder;
import edu.wustl.scout.xnat.auth.ScoutAuthProperties;
import org.junit.Before;
import org.junit.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.fail;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

public class TokenExchangeServiceTest {

    private static final String TOKEN_URI = "https://kc.example/realms/scout/protocol/openid-connect/token";
    private static final String CLIENT_ID = "xnat";
    private static final String CLIENT_SECRET = "s3cr3t";

    private ScoutAuthProperties properties;
    private RestTemplate restTemplate;
    private Cache<String, TokenExchangeService.CachedExchange> cache;
    private TokenExchangeService service;

    @Before
    public void setUp() {
        properties = new ScoutAuthProperties();
        ReflectionTestUtils.setField(properties, "tokenUri", TOKEN_URI);
        ReflectionTestUtils.setField(properties, "clientId", CLIENT_ID);
        ReflectionTestUtils.setField(properties, "clientSecret", CLIENT_SECRET);

        restTemplate = mock(RestTemplate.class);
        cache = CacheBuilder.newBuilder().maximumSize(8).build();
        service = new TokenExchangeService(properties, restTemplate, cache);
    }

    @Test
    public void exchange_postsCorrectFormAndBasicAuth() throws Exception {
        stubExchangeResponse("exchanged-jwt", 300L);

        String token = service.exchange("subject-jwt");
        assertEquals("exchanged-jwt", token);

        ArgumentCaptor<HttpEntity<MultiValueMap<String, String>>> captor = entityCaptor();
        verify(restTemplate).exchange(eq(TOKEN_URI), eq(HttpMethod.POST), captor.capture(), eq(JsonNode.class));
        HttpEntity<MultiValueMap<String, String>> entity = captor.getValue();
        MultiValueMap<String, String> body = entity.getBody();

        assertEquals("urn:ietf:params:oauth:grant-type:token-exchange", body.getFirst("grant_type"));
        assertEquals("urn:ietf:params:oauth:token-type:access_token", body.getFirst("subject_token_type"));
        assertEquals("subject-jwt", body.getFirst("subject_token"));
        // No `audience` param: Keycloak rejects audience=<own client_id>.
        assertEquals(null, body.getFirst("audience"));

        HttpHeaders headers = entity.getHeaders();
        assertEquals(TokenExchangeService.basicAuth(CLIENT_ID, CLIENT_SECRET),
                headers.getFirst(HttpHeaders.AUTHORIZATION));
    }

    @Test
    public void exchange_cachesByJwtHash() throws Exception {
        stubExchangeResponse("exchanged-jwt", 300L);

        service.exchange("subject-jwt");
        service.exchange("subject-jwt");

        // Second call should hit cache → exactly one upstream POST.
        verify(restTemplate, times(1))
                .exchange(eq(TOKEN_URI), eq(HttpMethod.POST), any(HttpEntity.class), eq(JsonNode.class));
    }

    @Test
    public void exchange_distinctSubjectTokensCacheSeparately() throws Exception {
        stubExchangeResponse("exchanged-a", 300L);
        service.exchange("subject-a");

        stubExchangeResponse("exchanged-b", 300L);
        service.exchange("subject-b");

        verify(restTemplate, times(2))
                .exchange(eq(TOKEN_URI), eq(HttpMethod.POST), any(HttpEntity.class), eq(JsonNode.class));
    }

    @Test
    public void exchange_skipsCacheWhenEntryIsNearingExpiry() throws Exception {
        // expires_in less than the safety margin — entry should be treated as
        // already-expired and re-fetched on the next call.
        stubExchangeResponse("exchanged-jwt", 5L);

        service.exchange("subject-jwt");
        service.exchange("subject-jwt");

        verify(restTemplate, times(2))
                .exchange(eq(TOKEN_URI), eq(HttpMethod.POST), any(HttpEntity.class), eq(JsonNode.class));
    }

    @Test
    public void exchange_throwsOnRestClientException() {
        when(restTemplate.exchange(eq(TOKEN_URI), eq(HttpMethod.POST), any(HttpEntity.class), eq(JsonNode.class)))
                .thenThrow(new RestClientException("boom"));
        try {
            service.exchange("subject-jwt");
            fail("expected TokenExchangeException");
        } catch (TokenExchangeService.TokenExchangeException e) {
            // ok
        }
    }

    @Test
    public void exchange_throwsOnMissingAccessToken() throws Exception {
        when(restTemplate.exchange(eq(TOKEN_URI), eq(HttpMethod.POST), any(HttpEntity.class), eq(JsonNode.class)))
                .thenReturn(ResponseEntity.ok(new ObjectMapper().readTree("{\"expires_in\":300}")));
        try {
            service.exchange("subject-jwt");
            fail("expected TokenExchangeException");
        } catch (TokenExchangeService.TokenExchangeException e) {
            // ok
        }
    }

    @Test
    public void exchange_throwsOnBlankSubjectToken() {
        try {
            service.exchange("");
            fail("expected TokenExchangeException");
        } catch (TokenExchangeService.TokenExchangeException e) {
            // ok
        }
    }

    @Test
    public void exchange_throwsWhenTokenUriUnconfigured() {
        ReflectionTestUtils.setField(properties, "tokenUri", "");
        try {
            service.exchange("subject-jwt");
            fail("expected TokenExchangeException");
        } catch (TokenExchangeService.TokenExchangeException e) {
            // ok
        }
    }

    @Test
    public void exchange_throwsWhenClientSecretUnconfigured() {
        ReflectionTestUtils.setField(properties, "clientSecret", "");
        try {
            service.exchange("subject-jwt");
            fail("expected TokenExchangeException");
        } catch (TokenExchangeService.TokenExchangeException e) {
            // ok
        }
    }

    @Test
    public void sha256Hex_isStableAndDistinct() {
        String a = TokenExchangeService.sha256Hex("alpha");
        String b = TokenExchangeService.sha256Hex("alpha");
        String c = TokenExchangeService.sha256Hex("beta");
        assertEquals(64, a.length());
        assertEquals(a, b);
        assertNotEquals(a, c);
    }

    private void stubExchangeResponse(String accessToken, long expiresIn) throws Exception {
        String json = String.format("{\"access_token\":\"%s\",\"expires_in\":%d}", accessToken, expiresIn);
        JsonNode body = new ObjectMapper().readTree(json);
        when(restTemplate.exchange(eq(TOKEN_URI), eq(HttpMethod.POST), any(HttpEntity.class), eq(JsonNode.class)))
                .thenReturn(ResponseEntity.ok(body));
    }

    @SuppressWarnings({"unchecked", "rawtypes"})
    private ArgumentCaptor<HttpEntity<MultiValueMap<String, String>>> entityCaptor() {
        return (ArgumentCaptor) ArgumentCaptor.forClass(HttpEntity.class);
    }
}
