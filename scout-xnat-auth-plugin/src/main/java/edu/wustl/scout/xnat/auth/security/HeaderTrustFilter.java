package edu.wustl.scout.xnat.auth.security;

import com.nimbusds.jwt.JWTClaimsSet;
import edu.wustl.scout.xnat.auth.ScoutAuthProperties;
import edu.wustl.scout.xnat.auth.model.ScoutIdentity;
import edu.wustl.scout.xnat.auth.service.UserProvisioningService;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import javax.servlet.FilterChain;
import javax.servlet.ServletException;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;

/**
 * Authenticates browser-path traffic carrying the access token oauth2-proxy
 * forwarded from its OIDC session ({@code X-Auth-Request-Access-Token}). The
 * token is the single source of truth for identity ({@code sub},
 * {@code preferred_username}, {@code email}, names) and roles
 * ({@code resource_access.<clientId>.roles} + {@code realm_access.roles}).
 *
 * <p>Cryptographic gate: the token is validated against Keycloak's JWKS
 * (signature + issuer + expiry) and its {@code azp} must match the
 * oauth2-proxy client. That gives us the same trust as the Bearer flow's
 * incoming-token check, plus a binding to the OIDC client the token was
 * issued for. We do not rely on the network shape (Traefik / ForwardAuth) as
 * a trust boundary — defense in depth, not the primary check.
 *
 * <p>Does nothing on requests that lack the token (in-cluster API traffic)
 * — the BearerTokenFilter handles those.
 *
 * <p>Status codes:
 * <ul>
 *   <li>401 — token present but fails sig/iss/exp, or {@code azp} doesn't
 *       match the oauth2-proxy client.</li>
 *   <li>403 — token validates but lacks the required role, or downstream
 *       provisioning rejects the user.</li>
 * </ul>
 */
@Slf4j
@Component
public class HeaderTrustFilter extends OncePerRequestFilter {

    private final ScoutAuthProperties properties;
    private final JwtValidator jwtValidator;
    private final UserProvisioningService provisioningService;

    public HeaderTrustFilter(final ScoutAuthProperties properties,
                             final JwtValidator jwtValidator,
                             final UserProvisioningService provisioningService) {
        this.properties = properties;
        this.jwtValidator = jwtValidator;
        this.provisioningService = provisioningService;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain chain)
            throws ServletException, IOException {

        // Short-circuit if a SecurityContext is already populated (eg. via
        // JSESSIONID + Spring's session-cached Authentication, or a previous
        // filter in this same chain).
        Authentication current = SecurityContextHolder.getContext().getAuthentication();
        if (current != null && current.isAuthenticated()) {
            chain.doFilter(request, response);
            return;
        }

        final String accessToken = request.getHeader(properties.getAccessTokenHeader());
        if (StringUtils.isBlank(accessToken)) {
            // No forwarded access token; not an oauth2-proxy browser request.
            // Leave the chain alone so the BearerTokenFilter / Spring's default
            // path can handle it.
            chain.doFilter(request, response);
            return;
        }

        final JWTClaimsSet claims;
        try {
            claims = jwtValidator.validate(accessToken);
        } catch (JwtValidator.InvalidJwtException e) {
            log.warn("HeaderTrustFilter rejecting request: forwarded access token is invalid ({})", e.getMessage());
            SecurityContextHolder.clearContext();
            response.sendError(HttpServletResponse.SC_UNAUTHORIZED, "Scout auth: invalid access token");
            return;
        }

        // The token must have been issued to oauth2-proxy. Pinning azp is what
        // makes this safe to trust as a browser-path token — any other client's
        // token (eg. a Bearer-flow caller smuggling it into the header) is
        // rejected here.
        final String azp = JwtValidator.claimAsString(claims, "azp");
        if (!properties.getOauth2ProxyClientId().equals(azp)) {
            log.warn("HeaderTrustFilter rejecting request: access token azp '{}' is not '{}'",
                    azp, properties.getOauth2ProxyClientId());
            SecurityContextHolder.clearContext();
            response.sendError(HttpServletResponse.SC_UNAUTHORIZED, "Scout auth: token not issued for browser flow");
            return;
        }

        final ScoutIdentity identity = ScoutAuthSupport.identityFrom(claims, properties.getClientId());

        // The xnat-access gate is enforced solely from the token's role claims:
        // client roles in resource_access.<client>.roles, plus realm roles in
        // realm_access.roles. No group-name inference fallback.
        log.info("HeaderTrustFilter saw roles {} in access token for sub '{}'",
                identity.getRoles(), identity.getSub());
        if (!identity.hasRole(properties.getRequiredRole())) {
            log.warn("HeaderTrustFilter rejecting request: access token for sub '{}' lacks required role '{}' (saw {})",
                    identity.getSub(), properties.getRequiredRole(), identity.getRoles());
            SecurityContextHolder.clearContext();
            response.sendError(HttpServletResponse.SC_FORBIDDEN, "Scout auth: missing required role");
            return;
        }

        log.debug("HeaderTrustFilter authenticating {}", identity);
        if (!ScoutAuthSupport.establishSession(request, response, provisioningService, identity, log)) {
            return;
        }

        chain.doFilter(request, response);
    }
}
