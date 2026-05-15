package edu.wustl.scout.xnat.auth.security;

import edu.wustl.scout.xnat.auth.ScoutAuthProperties;
import edu.wustl.scout.xnat.auth.service.UserProvisioningService;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.mockito.MockedStatic;
import org.nrg.xdat.security.helpers.UserHelper;
import org.nrg.xft.security.UserI;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.test.util.ReflectionTestUtils;

import javax.servlet.FilterChain;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertSame;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Smoke tests for {@link HeaderTrustFilter}, with the same Phase 6 focus
 * as {@link BearerTokenFilterTest}: after authenticate-success the filter
 * must populate the session-scoped {@link UserHelper} so the
 * Velocity-rendered project page sees a non-null permission model on
 * subsequent renders.
 *
 * Browser traffic (oauth2-proxy → XNAT) authenticates here; the demo
 * end-to-end exercise on dev03 showed this is the actually-relevant
 * filter for the security-warning bug — see phase-6 retrospective.
 */
public class HeaderTrustFilterTest {

    private static final String CLIENT_ID = "xnat";
    private static final String REQUIRED_ROLE = "xnat-access";

    private ScoutAuthProperties properties;
    private UserProvisioningService provisioningService;
    private HeaderTrustFilter filter;

    @Before
    public void setUp() {
        properties = new ScoutAuthProperties();
        ReflectionTestUtils.setField(properties, "clientId", CLIENT_ID);
        ReflectionTestUtils.setField(properties, "requiredRole", REQUIRED_ROLE);
        ReflectionTestUtils.setField(properties, "userHeader", "X-Auth-Request-User");
        ReflectionTestUtils.setField(properties, "emailHeader", "X-Auth-Request-Email");
        ReflectionTestUtils.setField(properties, "groupsHeader", "X-Auth-Request-Groups");
        ReflectionTestUtils.setField(properties, "accessTokenHeader", "X-Auth-Request-Access-Token");

        provisioningService = mock(UserProvisioningService.class);
        filter = new HeaderTrustFilter(properties, provisioningService);

        SecurityContextHolder.clearContext();
    }

    @After
    public void tearDown() {
        SecurityContextHolder.clearContext();
    }

    @Test
    public void successPath_setsSecurityContextAndPopulatesUserHelper() throws Exception {
        MockHttpServletRequest request = oauth2ProxyRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        UserI user = mock(UserI.class);
        when(provisioningService.provision(any())).thenReturn(user);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            verify(chain, times(1)).doFilter(request, response);
            assertSame(user, SecurityContextHolder.getContext().getAuthentication().getPrincipal());
            userHelperStatic.verify(() -> UserHelper.setUserHelper(request, user), times(1));
        }
    }

    @Test
    public void noOauth2ProxyHeaders_skipsAuthAndDoesNotPopulateUserHelper() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        try (MockedStatic<UserHelper> userHelperStatic = org.mockito.Mockito.mockStatic(UserHelper.class)) {
            filter.doFilter(request, response, chain);

            verify(chain, times(1)).doFilter(request, response);
            userHelperStatic.verify(() -> UserHelper.setUserHelper(any(), any()), never());
        }
    }

    @Test
    public void provisionFails_returns403AndDoesNotPopulateUserHelper() throws Exception {
        MockHttpServletRequest request = oauth2ProxyRequest();
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
        MockHttpServletRequest request = oauth2ProxyRequest();
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

    private static MockHttpServletRequest oauth2ProxyRequest() {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/data/projects/SCOUT-DEMO");
        request.addHeader("X-Auth-Request-User", "scout-authorized-test-user");
        request.addHeader("X-Auth-Request-Email", "alice@example.org");
        request.addHeader("X-Auth-Request-Groups", "scout-user");
        return request;
    }
}
