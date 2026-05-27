package edu.wustl.scout.xnat.auth.security;

import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.PlainJWT;
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
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Tests for {@link HeaderTrustFilter} after the token-only refactor
 * (docs/internal/xnat-auth-header-trust-token-only-refactor.md). The filter
 * now trusts a single forwarded access token as the source of truth for
 * identity and the required-role gate; the former User/Email/Groups header
 * fallbacks are gone.
 *
 * As in {@link BearerTokenFilterTest}, the success path must populate the
 * session-scoped {@link UserHelper} so the Velocity-rendered project page
 * sees a non-null permission model on subsequent renders.
 */
public class HeaderTrustFilterTest {

    private static final String CLIENT_ID = "xnat";
    private static final String REQUIRED_ROLE = "xnat-access";
    private static final String ACCESS_TOKEN_HEADER = "X-Auth-Request-Access-Token";

    private ScoutAuthProperties properties;
    private UserProvisioningService provisioningService;
    private HeaderTrustFilter filter;

    @Before
    public void setUp() {
        properties = new ScoutAuthProperties();
        ReflectionTestUtils.setField(properties, "clientId", CLIENT_ID);
        ReflectionTestUtils.setField(properties, "requiredRole", REQUIRED_ROLE);
        ReflectionTestUtils.setField(properties, "accessTokenHeader", ACCESS_TOKEN_HEADER);

        provisioningService = mock(UserProvisioningService.class);
        filter = new HeaderTrustFilter(properties, provisioningService);

        SecurityContextHolder.clearContext();
    }

    @After
    public void tearDown() {
        SecurityContextHolder.clearContext();
    }

    /** Test #1: token alone authenticates and populates the full identity. */
    @Test
    public void accessTokenOnly_authsAndPopulatesEverythingFromJwt() throws Exception {
        MockHttpServletRequest request = requestWithToken(fullClaimsJwt());
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        UserI user = mock(UserI.class);
        when(provisioningService.provision(any())).thenReturn(user);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            verify(chain, times(1)).doFilter(any(), any());
            assertSame(user, SecurityContextHolder.getContext().getAuthentication().getPrincipal());
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), org.mockito.ArgumentMatchers.eq(user)), times(1));
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

    /** Test #2: no headers at all → chain continues, context untouched. */
    @Test
    public void missingAccessToken_skipsAuth() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            verify(chain, times(1)).doFilter(request, response);
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), any()), never());
        }
        assertNull(SecurityContextHolder.getContext().getAuthentication());
    }

    /** Test #3: present-but-unparseable token → 401, chain not continued. */
    @Test
    public void unparseableAccessToken_returns401() throws Exception {
        MockHttpServletRequest request = requestWithToken("not-a-jwt");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            assertEquals(401, response.getStatus());
            verify(chain, never()).doFilter(any(), any());
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), any()), never());
        }
        assertNull(SecurityContextHolder.getContext().getAuthentication());
    }

    /** Test #4: token parses but lacks the required role → 403. */
    @Test
    public void tokenMissingRequiredRole_returns403() throws Exception {
        JWTClaimsSet claims = new JWTClaimsSet.Builder()
                .subject("subject-123")
                .claim("preferred_username", "alice")
                .claim("resource_access", Collections.singletonMap(CLIENT_ID,
                        Collections.singletonMap("roles", Collections.singletonList("some-other-role"))))
                .claim("realm_access", Collections.singletonMap("roles",
                        Collections.singletonList("offline_access")))
                .build();
        MockHttpServletRequest request = requestWithToken(jwt(claims));
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

    /** Test #5: realm role alone (no client role) satisfies the gate. */
    @Test
    public void tokenWithRealmRoleOnly_authsSuccessfully() throws Exception {
        JWTClaimsSet claims = new JWTClaimsSet.Builder()
                .subject("subject-123")
                .claim("preferred_username", "alice")
                .claim("realm_access", Collections.singletonMap("roles",
                        Collections.singletonList(REQUIRED_ROLE)))
                .build();
        MockHttpServletRequest request = requestWithToken(jwt(claims));
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        UserI user = mock(UserI.class);
        when(provisioningService.provision(any())).thenReturn(user);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            verify(chain, times(1)).doFilter(any(), any());
            assertSame(user, SecurityContextHolder.getContext().getAuthentication().getPrincipal());
        }
    }

    /** Test #6: missing email claim → auth still succeeds with null email. */
    @Test
    public void tokenWithEmailNullInClaims_authsWithEmptyEmail() throws Exception {
        JWTClaimsSet claims = new JWTClaimsSet.Builder()
                .subject("subject-123")
                .claim("preferred_username", "alice")
                .claim("resource_access", Collections.singletonMap(CLIENT_ID,
                        Collections.singletonMap("roles", Collections.singletonList(REQUIRED_ROLE))))
                .build();
        MockHttpServletRequest request = requestWithToken(jwt(claims));
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
     * §5.2 (R4): during the transitional deploy window the legacy
     * User/Email/Groups headers may still arrive alongside the token. The
     * token must win and the legacy headers must be ignored entirely.
     */
    @Test
    public void legacyHeadersWithToken_usesTokenIgnoresHeaders() throws Exception {
        MockHttpServletRequest request = requestWithToken(fullClaimsJwt());
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
        MockHttpServletRequest request = requestWithToken(fullClaimsJwt());
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
        MockHttpServletRequest request = requestWithToken(fullClaimsJwt());
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        org.springframework.security.core.Authentication existing =
                mock(org.springframework.security.core.Authentication.class);
        when(existing.isAuthenticated()).thenReturn(true);
        SecurityContextHolder.getContext().setAuthentication(existing);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            verify(chain, times(1)).doFilter(request, response);
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

    /** Serializes claims as an unsigned (plain) JWT — the filter does not validate signatures. */
    private static String jwt(JWTClaimsSet claims) {
        return new PlainJWT(claims).serialize();
    }

    private static String fullClaimsJwt() {
        Map<String, Object> clientRoles = Collections.singletonMap("roles",
                Collections.singletonList(REQUIRED_ROLE));
        JWTClaimsSet claims = new JWTClaimsSet.Builder()
                .subject("subject-123")
                .claim("preferred_username", "alice")
                .claim("email", "alice@example.org")
                .claim("given_name", "Alice")
                .claim("family_name", "Anderson")
                .claim("resource_access", Collections.singletonMap(CLIENT_ID, clientRoles))
                .build();
        return jwt(claims);
    }
}
