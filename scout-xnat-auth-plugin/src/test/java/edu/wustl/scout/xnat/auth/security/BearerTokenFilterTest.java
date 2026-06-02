package edu.wustl.scout.xnat.auth.security;

import com.nimbusds.jwt.JWTClaimsSet;
import edu.wustl.scout.xnat.auth.ScoutAuthProperties;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.mockito.MockedStatic;
import org.nrg.xdat.security.helpers.UserHelper;
import org.nrg.xft.security.UserI;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.authentication.AuthenticationServiceException;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.test.util.ReflectionTestUtils;

import javax.servlet.FilterChain;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertSame;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Smoke tests for the success/failure paths of {@link BearerTokenFilter},
 * with a particular eye on Phase 6: after authenticate-success the filter
 * must invoke {@link UserHelper#setUserHelper} so the browser/Velocity path
 * sees a populated permission model on subsequent project-page renders.
 *
 * The {@code UserHelper.setUserHelper} call goes through static XNAT bean
 * lookup that isn't available in a unit-test JVM, so we use Mockito's
 * static mocking to verify the call site without exercising XDAT's
 * runtime bean wiring. End-to-end behavior (the session attribute really
 * does land on the HTTP session and Velocity really does render the
 * project page) is covered by the manual phase-6 browser exercise on
 * dev03.
 */
public class BearerTokenFilterTest {

    private static final String CLIENT_ID = "xnat";
    private static final String REQUIRED_ROLE = "xnat-access";

    private ScoutAuthProperties properties;
    private JwtValidator jwtValidator;
    private edu.wustl.scout.xnat.auth.service.UserProvisioningService provisioningService;
    private BearerTokenFilter filter;

    @Before
    public void setUp() {
        properties = new ScoutAuthProperties();
        ReflectionTestUtils.setField(properties, "clientId", CLIENT_ID);
        ReflectionTestUtils.setField(properties, "requiredRole", REQUIRED_ROLE);

        jwtValidator = mock(JwtValidator.class);
        provisioningService = mock(edu.wustl.scout.xnat.auth.service.UserProvisioningService.class);

        filter = new BearerTokenFilter(properties, jwtValidator, provisioningService);

        SecurityContextHolder.clearContext();
    }

    @After
    public void tearDown() {
        SecurityContextHolder.clearContext();
    }

    @Test
    public void successPath_setsSecurityContextAndPopulatesUserHelper() throws Exception {
        MockHttpServletRequest request = bearerRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        JWTClaimsSet claims = claims()
                .subject("f077e17f")
                .audience(CLIENT_ID)
                .claim("preferred_username", "alice")
                .claim("email", "alice@example.org")
                .claim("resource_access", clientRoles(CLIENT_ID, REQUIRED_ROLE))
                .build();
        when(jwtValidator.validate("subject-jwt")).thenReturn(claims);

        UserI user = mock(UserI.class);
        when(provisioningService.provision(any())).thenReturn(user);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            // Filter chain proceeds.
            verify(chain, times(1)).doFilter(request, response);

            // Spring security context populated with our token.
            assertNotNull(SecurityContextHolder.getContext().getAuthentication());
            assertSame(user, SecurityContextHolder.getContext().getAuthentication().getPrincipal());

            // Phase 6: UserHelper.setUserHelper is called exactly once with this
            // request and the resolved user, mirroring XDAT.loginUser.
            userHelperStatic.verify(() -> UserHelper.setUserHelper(request, user), times(1));
        }
    }

    @Test
    public void noBearer_skipsAuthAndDoesNotPopulateUserHelper() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/data/projects");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            verify(chain, times(1)).doFilter(request, response);
            // Filter is a no-op when there's no Authorization: Bearer header — must
            // not touch UserHelper or the SecurityContext.
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), any()), never());
        }
        assertEquals(null, SecurityContextHolder.getContext().getAuthentication());
    }

    @Test
    public void invalidBearerJwt_returns401AndDoesNotPopulateUserHelper() throws Exception {
        MockHttpServletRequest request = bearerRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        org.mockito.Mockito.doThrow(new JwtValidator.InvalidJwtException("expired"))
                .when(jwtValidator).validate("subject-jwt");

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            assertEquals(401, response.getStatus());
            verify(chain, never()).doFilter(any(), any());
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), any()), never());
        }
    }

    /**
     * A signature-valid token whose {@code aud} does not contain the xnat
     * client is rejected with 403 — the audience is the API path's confinement
     * boundary, and provisioning must not run.
     */
    @Test
    public void tokenWithoutXnatAudience_returns403() throws Exception {
        MockHttpServletRequest request = bearerRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        JWTClaimsSet claims = claims()
                .subject("f077e17f")
                .audience("some-other-client")
                .claim("resource_access", clientRoles(CLIENT_ID, REQUIRED_ROLE))
                .build();
        when(jwtValidator.validate("subject-jwt")).thenReturn(claims);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            assertEquals(403, response.getStatus());
            verify(chain, never()).doFilter(any(), any());
            verify(provisioningService, never()).provision(any());
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), any()), never());
        }
    }

    /**
     * A token correctly scoped to xnat but lacking the {@code xnat-access}
     * client role is rejected with 403 before provisioning.
     */
    @Test
    public void tokenWithoutRequiredRole_returns403() throws Exception {
        MockHttpServletRequest request = bearerRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        JWTClaimsSet claims = claims()
                .subject("f077e17f")
                .audience(CLIENT_ID)
                .claim("resource_access", clientRoles(CLIENT_ID, "xnat-user"))  // wrong role
                .build();
        when(jwtValidator.validate("subject-jwt")).thenReturn(claims);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            assertEquals(403, response.getStatus());
            verify(chain, never()).doFilter(any(), any());
            verify(provisioningService, never()).provision(any());
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), any()), never());
        }
    }

    @Test
    public void provisionFails_returns403AndDoesNotPopulateUserHelper() throws Exception {
        MockHttpServletRequest request = bearerRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        when(jwtValidator.validate("subject-jwt")).thenReturn(validClaims("f077e17f"));
        when(provisioningService.provision(any()))
                .thenThrow(new AuthenticationServiceException("scout-user role required"));

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            assertEquals(403, response.getStatus());
            verify(chain, never()).doFilter(any(), any());
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), any()), never());
        }
        // SecurityContext is cleared on provisioning failure.
        assertFalse(SecurityContextHolder.getContext().getAuthentication() != null
                && SecurityContextHolder.getContext().getAuthentication().isAuthenticated());
    }

    /**
     * The role gate inside DefaultUserProvisioningService throws
     * {@link AccessDeniedException} — a {@code RuntimeException}, not an
     * {@code AuthenticationException}. The filter (via the shared
     * establishSession helper, which catches both) must still turn it into a
     * 403 rather than letting it escape as a 500.
     */
    @Test
    public void provisionFailsWithAccessDenied_returns403() throws Exception {
        MockHttpServletRequest request = bearerRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        when(jwtValidator.validate("subject-jwt")).thenReturn(validClaims("f077e17f"));
        when(provisioningService.provision(any()))
                .thenThrow(new AccessDeniedException("user lacks xnat-access role"));

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            assertEquals(403, response.getStatus());
            verify(chain, never()).doFilter(any(), any());
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), any()), never());
        }
        assertFalse(SecurityContextHolder.getContext().getAuthentication() != null
                && SecurityContextHolder.getContext().getAuthentication().isAuthenticated());
    }

    @Test
    public void preExistingSecurityContext_shortCircuitsAndDoesNotPopulateUserHelper() throws Exception {
        MockHttpServletRequest request = bearerRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        org.springframework.security.core.Authentication existing =
                mock(org.springframework.security.core.Authentication.class);
        when(existing.isAuthenticated()).thenReturn(true);
        SecurityContextHolder.getContext().setAuthentication(existing);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            // Already-authenticated callers (eg. JSESSIONID-bearing browsers
            // returning to a populated session) skip the bearer pipeline.
            verify(chain, times(1)).doFilter(request, response);
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), any()), never());
            assertSame(existing, SecurityContextHolder.getContext().getAuthentication());
        }
    }

    private static MockHttpServletRequest bearerRequest() {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/data/projects");
        request.addHeader("Authorization", "Bearer subject-jwt");
        return request;
    }

    /** Claims correctly scoped for xnat (aud + xnat-access role). */
    private static JWTClaimsSet validClaims(String sub) {
        return claims()
                .subject(sub)
                .audience(CLIENT_ID)
                .claim("resource_access", clientRoles(CLIENT_ID, REQUIRED_ROLE))
                .build();
    }

    private static JWTClaimsSet.Builder claims() {
        return new JWTClaimsSet.Builder();
    }

    private static Map<String, Object> clientRoles(String clientId, String... roles) {
        Map<String, Object> resourceAccess = new HashMap<>();
        Map<String, Object> client = new HashMap<>();
        client.put("roles", roles.length == 0 ? Collections.emptyList() : Arrays.asList(roles));
        resourceAccess.put(clientId, client);
        return resourceAccess;
    }
}
