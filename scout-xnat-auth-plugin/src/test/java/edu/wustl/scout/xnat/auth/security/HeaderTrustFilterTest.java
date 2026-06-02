package edu.wustl.scout.xnat.auth.security;

import com.nimbusds.jwt.JWTClaimsSet;
import edu.wustl.scout.xnat.auth.ScoutAuthProperties;
import edu.wustl.scout.xnat.auth.model.ScoutIdentity;
import edu.wustl.scout.xnat.auth.service.UserProvisioningService;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.MockedStatic;
import org.nrg.xdat.security.helpers.UserHelper;
import org.nrg.xft.security.UserI;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.test.util.ReflectionTestUtils;

import javax.servlet.FilterChain;
import java.util.Collections;
import java.util.Map;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertSame;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Tests for {@link HeaderTrustFilter} after the cryptographic-hardening
 * change: the filter now validates the forwarded access token against
 * Keycloak's JWKS (sig + iss + exp) via {@link JwtValidator} and pins
 * {@code azp} to the oauth2-proxy client.
 *
 * <p>{@link JwtValidator} is mocked here; its own behavior is covered by
 * {@code JwtValidatorTest}. The filter tests just need to know whether
 * validation succeeded and what claims came back.
 *
 * <p>The success path must populate the session-scoped {@link UserHelper}
 * so the Velocity-rendered project page sees a non-null permission model
 * on subsequent renders.
 */
public class HeaderTrustFilterTest {

    private static final String CLIENT_ID = "xnat";
    private static final String OAUTH2_PROXY_CLIENT_ID = "oauth2-proxy";
    private static final String REQUIRED_ROLE = "xnat-access";
    private static final String ACCESS_TOKEN_HEADER = "X-Auth-Request-Access-Token";

    private ScoutAuthProperties properties;
    private JwtValidator jwtValidator;
    private UserProvisioningService provisioningService;
    private HeaderTrustFilter filter;

    @Before
    public void setUp() {
        properties = new ScoutAuthProperties();
        ReflectionTestUtils.setField(properties, "clientId", CLIENT_ID);
        ReflectionTestUtils.setField(properties, "oauth2ProxyClientId", OAUTH2_PROXY_CLIENT_ID);
        ReflectionTestUtils.setField(properties, "requiredRole", REQUIRED_ROLE);
        ReflectionTestUtils.setField(properties, "accessTokenHeader", ACCESS_TOKEN_HEADER);

        jwtValidator = mock(JwtValidator.class);
        provisioningService = mock(UserProvisioningService.class);
        filter = new HeaderTrustFilter(properties, jwtValidator, provisioningService);

        SecurityContextHolder.clearContext();
    }

    @After
    public void tearDown() {
        SecurityContextHolder.clearContext();
    }

    /** Valid token + correct azp + role → authenticates and populates identity from JWT. */
    @Test
    public void validToken_authsAndPopulatesEverythingFromJwt() throws Exception {
        when(jwtValidator.validate("token-value")).thenReturn(fullClaims());

        MockHttpServletRequest request = requestWithToken("token-value");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        UserI user = mock(UserI.class);
        when(provisioningService.provision(any())).thenReturn(user);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            verify(chain, times(1)).doFilter(any(), any());
            assertSame(user, SecurityContextHolder.getContext().getAuthentication().getPrincipal());
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), eq(user)), times(1));
        }

        ArgumentCaptor<ScoutIdentity> captor = ArgumentCaptor.forClass(ScoutIdentity.class);
        verify(provisioningService).provision(captor.capture());
        ScoutIdentity identity = captor.getValue();
        assertEquals("subject-123", identity.getSub());
        assertEquals("alice", identity.getPreferredUsername());
        assertEquals("alice@example.org", identity.getEmail());
        assertEquals("Alice", identity.getFirstName());
        assertEquals("Anderson", identity.getLastName());
    }

    /** No token header → chain continues, validator never called, context untouched. */
    @Test
    public void missingAccessToken_skipsAuth() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            verify(chain, times(1)).doFilter(request, response);
            verify(jwtValidator, never()).validate(any());
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), any()), never());
        }
        assertNull(SecurityContextHolder.getContext().getAuthentication());
    }

    /** Validator rejects the token (bad sig / iss / exp / malformed) → 401. */
    @Test
    public void invalidJwt_returns401() throws Exception {
        when(jwtValidator.validate("bad-token"))
                .thenThrow(new JwtValidator.InvalidJwtException("signature mismatch"));

        MockHttpServletRequest request = requestWithToken("bad-token");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            assertEquals(401, response.getStatus());
            verify(chain, never()).doFilter(any(), any());
            verify(provisioningService, never()).provision(any());
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), any()), never());
        }
        assertNull(SecurityContextHolder.getContext().getAuthentication());
    }

    /** Token validates but azp is some other client (eg. jupyterhub) → 401. */
    @Test
    public void wrongAzp_returns401() throws Exception {
        JWTClaimsSet claims = baseClaimsBuilder()
                .claim("azp", "jupyterhub")
                .build();
        when(jwtValidator.validate("jh-token")).thenReturn(claims);

        MockHttpServletRequest request = requestWithToken("jh-token");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            assertEquals(401, response.getStatus());
            verify(chain, never()).doFilter(any(), any());
            verify(provisioningService, never()).provision(any());
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), any()), never());
        }
        assertNull(SecurityContextHolder.getContext().getAuthentication());
    }

    /** Token validates but has no azp claim at all → 401. */
    @Test
    public void missingAzp_returns401() throws Exception {
        JWTClaimsSet claims = baseClaimsBuilder().build();  // no azp
        when(jwtValidator.validate("no-azp-token")).thenReturn(claims);

        MockHttpServletRequest request = requestWithToken("no-azp-token");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        filter.doFilter(request, response, chain);

        assertEquals(401, response.getStatus());
        verify(chain, never()).doFilter(any(), any());
        verify(provisioningService, never()).provision(any());
    }

    /** Token validates, azp matches, but no xnat-access role → 403. */
    @Test
    public void tokenMissingRequiredRole_returns403() throws Exception {
        JWTClaimsSet claims = new JWTClaimsSet.Builder()
                .subject("subject-123")
                .claim("azp", OAUTH2_PROXY_CLIENT_ID)
                .claim("preferred_username", "alice")
                .claim("resource_access", Collections.singletonMap(CLIENT_ID,
                        Collections.singletonMap("roles", Collections.singletonList("some-other-role"))))
                .claim("realm_access", Collections.singletonMap("roles",
                        Collections.singletonList("offline_access")))
                .build();
        when(jwtValidator.validate("role-less-token")).thenReturn(claims);

        MockHttpServletRequest request = requestWithToken("role-less-token");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            assertEquals(403, response.getStatus());
            verify(chain, never()).doFilter(any(), any());
            verify(provisioningService, never()).provision(any());
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), any()), never());
        }
        assertNull(SecurityContextHolder.getContext().getAuthentication());
    }

    /**
     * xnat-access is a Keycloak client role. A token carrying it only as a
     * realm role (realm_access.roles) must NOT satisfy the gate — we consult
     * client roles (resource_access.<client>.roles) only — so it is rejected
     * with 403 and provisioning is never attempted.
     */
    @Test
    public void realmRoleOnly_isRejected_returns403() throws Exception {
        JWTClaimsSet claims = new JWTClaimsSet.Builder()
                .subject("subject-123")
                .claim("azp", OAUTH2_PROXY_CLIENT_ID)
                .claim("preferred_username", "alice")
                .claim("realm_access", Collections.singletonMap("roles",
                        Collections.singletonList(REQUIRED_ROLE)))
                .build();
        when(jwtValidator.validate("realm-role-token")).thenReturn(claims);

        MockHttpServletRequest request = requestWithToken("realm-role-token");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            assertEquals(403, response.getStatus());
            verify(chain, never()).doFilter(any(), any());
            verify(provisioningService, never()).provision(any());
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), any()), never());
        }
        assertNull(SecurityContextHolder.getContext().getAuthentication());
    }

    /** Missing email claim → auth still succeeds with null email. */
    @Test
    public void tokenWithEmailNullInClaims_authsWithEmptyEmail() throws Exception {
        JWTClaimsSet claims = new JWTClaimsSet.Builder()
                .subject("subject-123")
                .claim("azp", OAUTH2_PROXY_CLIENT_ID)
                .claim("preferred_username", "alice")
                .claim("resource_access", Collections.singletonMap(CLIENT_ID,
                        Collections.singletonMap("roles", Collections.singletonList(REQUIRED_ROLE))))
                .build();
        when(jwtValidator.validate("no-email-token")).thenReturn(claims);

        MockHttpServletRequest request = requestWithToken("no-email-token");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        when(provisioningService.provision(any())).thenReturn(mock(UserI.class));

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);
            verify(chain, times(1)).doFilter(any(), any());
        }

        ArgumentCaptor<ScoutIdentity> captor = ArgumentCaptor.forClass(ScoutIdentity.class);
        verify(provisioningService).provision(captor.capture());
        assertNull(captor.getValue().getEmail());
    }

    /**
     * Legacy User/Email/Groups headers must be ignored — the token wins.
     * (Carried over from the token-only refactor's R4 acceptance.)
     */
    @Test
    public void legacyHeadersWithToken_usesTokenIgnoresHeaders() throws Exception {
        when(jwtValidator.validate("token-value")).thenReturn(fullClaims());

        MockHttpServletRequest request = requestWithToken("token-value");
        request.addHeader("X-Auth-Request-User", "bob");
        request.addHeader("X-Auth-Request-Email", "bob@x.org");
        request.addHeader("X-Auth-Request-Groups", "scout-admin");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        when(provisioningService.provision(any())).thenReturn(mock(UserI.class));

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);
            verify(chain, times(1)).doFilter(any(), any());
        }

        ArgumentCaptor<ScoutIdentity> captor = ArgumentCaptor.forClass(ScoutIdentity.class);
        verify(provisioningService).provision(captor.capture());
        ScoutIdentity identity = captor.getValue();
        assertEquals("alice", identity.getPreferredUsername());
        assertEquals("alice@example.org", identity.getEmail());
    }

    @Test
    public void provisionFails_returns403AndDoesNotPopulateUserHelper() throws Exception {
        when(jwtValidator.validate("token-value")).thenReturn(fullClaims());

        MockHttpServletRequest request = requestWithToken("token-value");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        when(provisioningService.provision(any()))
                .thenThrow(new AccessDeniedException("scout-user role required"));

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            assertEquals(403, response.getStatus());
            verify(chain, never()).doFilter(any(), any());
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), any()), never());
        }
    }

    @Test
    public void preExistingSecurityContext_shortCircuitsAndDoesNotPopulateUserHelper() throws Exception {
        MockHttpServletRequest request = requestWithToken("token-value");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        org.springframework.security.core.Authentication existing =
                mock(org.springframework.security.core.Authentication.class);
        when(existing.isAuthenticated()).thenReturn(true);
        SecurityContextHolder.getContext().setAuthentication(existing);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            verify(chain, times(1)).doFilter(request, response);
            verify(jwtValidator, never()).validate(any());
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), any()), never());
            assertSame(existing, SecurityContextHolder.getContext().getAuthentication());
        }
    }

    // --- helpers ---

    private static MockHttpServletRequest requestWithToken(String token) {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/data/projects/SCOUT-DEMO");
        request.addHeader(ACCESS_TOKEN_HEADER, token);
        return request;
    }

    /** Claims with sub + azp=oauth2-proxy + preferred_username but no roles/email. */
    private static JWTClaimsSet.Builder baseClaimsBuilder() {
        return new JWTClaimsSet.Builder()
                .subject("subject-123")
                .claim("preferred_username", "alice");
    }

    private static JWTClaimsSet fullClaims() {
        Map<String, Object> clientRoles = Collections.singletonMap("roles",
                Collections.singletonList(REQUIRED_ROLE));
        return new JWTClaimsSet.Builder()
                .subject("subject-123")
                .claim("azp", OAUTH2_PROXY_CLIENT_ID)
                .claim("preferred_username", "alice")
                .claim("email", "alice@example.org")
                .claim("given_name", "Alice")
                .claim("family_name", "Anderson")
                .claim("resource_access", Collections.singletonMap(CLIENT_ID, clientRoles))
                .build();
    }
}
