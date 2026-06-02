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
import java.util.List;

/**
 * Authenticates in-cluster API callers carrying {@code Authorization: Bearer}.
 * The token is validated directly by audience — there is no server-side token
 * exchange:
 *   1. Validate the token against Keycloak's JWKS (sig + iss + exp).
 *   2. Require {@code aud} to contain the xnat client. Unlike the browser path,
 *      there is no network trust boundary here (any in-cluster caller can send
 *      a Bearer header), so the audience is what confines the token to xnat:
 *      Keycloak only emits {@code aud=xnat} for clients carrying the
 *      {@code xnat-audience} mapper (eg. jupyterhub). We do NOT check
 *      {@code azp} — the caller's authorized party is its own client (eg.
 *      jupyterhub), not xnat.
 *   3. Require the {@code xnat-access} client role
 *      ({@code resource_access.<clientId>.roles}).
 *   4. Provision/look up the XNAT user via {@link UserProvisioningService} and
 *      attach the principal.
 *
 * <p>Short-circuits on a populated SecurityContext (so a session cookie skips
 * us) or absent {@code Authorization: Bearer} (so the chain falls through to
 * Spring's default unauthenticated path on browser/empty calls).
 *
 * <p>Status codes:
 * <ul>
 *   <li>401 — token present but fails sig/iss/exp.</li>
 *   <li>403 — token validates but {@code aud} doesn't contain xnat, lacks the
 *       required role, or downstream provisioning rejects the user.</li>
 * </ul>
 */
@Slf4j
@Component
public class BearerTokenFilter extends OncePerRequestFilter {

    private static final String BEARER_PREFIX = "Bearer ";

    private final ScoutAuthProperties properties;
    private final JwtValidator jwtValidator;
    private final UserProvisioningService provisioningService;

    public BearerTokenFilter(final ScoutAuthProperties properties,
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

        Authentication current = SecurityContextHolder.getContext().getAuthentication();
        if (current != null && current.isAuthenticated()) {
            chain.doFilter(request, response);
            return;
        }

        String authHeader = request.getHeader("Authorization");
        if (StringUtils.isBlank(authHeader) || !authHeader.startsWith(BEARER_PREFIX)) {
            chain.doFilter(request, response);
            return;
        }
        final String bearerJwt = authHeader.substring(BEARER_PREFIX.length()).trim();

        // Step 1: validate signature/iss/exp.
        final JWTClaimsSet claims;
        try {
            claims = jwtValidator.validate(bearerJwt);
        } catch (JwtValidator.InvalidJwtException e) {
            log.info("BearerTokenFilter rejecting request: bearer token invalid ({})", e.getMessage());
            response.sendError(HttpServletResponse.SC_UNAUTHORIZED, "invalid bearer token");
            return;
        }

        // Step 2: the token must be addressed to the xnat client. The audience
        // is the confinement boundary on the API path (no network trust here).
        final List<String> audiences = claims.getAudience();
        if (audiences == null || !audiences.contains(properties.getClientId())) {
            log.warn("BearerTokenFilter rejecting request: token aud {} does not contain '{}'",
                    audiences, properties.getClientId());
            response.sendError(HttpServletResponse.SC_FORBIDDEN, "Scout auth: token not scoped for xnat");
            return;
        }

        final ScoutIdentity identity = ScoutAuthSupport.identityFrom(claims, properties.getClientId());

        // Step 3: the xnat-access gate, from the token's client-role claim only
        // (resource_access.<client>.roles). xnat-access is a Keycloak client
        // role on the xnat client, so realm roles are intentionally not consulted.
        log.info("BearerTokenFilter saw roles {} in bearer token for sub '{}'",
                identity.getRoles(), identity.getSub());
        if (!identity.hasRole(properties.getRequiredRole())) {
            log.warn("BearerTokenFilter rejecting request: bearer token for sub '{}' lacks required role '{}' (saw {})",
                    identity.getSub(), properties.getRequiredRole(), identity.getRoles());
            response.sendError(HttpServletResponse.SC_FORBIDDEN, "Scout auth: missing required role");
            return;
        }

        // Step 4: provision and attach.
        log.debug("BearerTokenFilter authenticating {}", identity);
        if (!ScoutAuthSupport.establishSession(request, response, provisioningService, identity, log)) {
            return;
        }

        chain.doFilter(request, response);
    }
}
