package edu.wustl.scout.xnat.auth.security;

import com.nimbusds.jwt.JWT;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.JWTParser;
import edu.wustl.scout.xnat.auth.ScoutAuthProperties;
import edu.wustl.scout.xnat.auth.model.ScoutIdentity;
import edu.wustl.scout.xnat.auth.service.UserProvisioningService;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
import org.nrg.xdat.security.helpers.UserHelper;
import org.nrg.xft.security.UserI;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import javax.servlet.FilterChain;
import javax.servlet.ServletException;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * Trusts the access token forwarded by oauth2-proxy on browser-path traffic.
 * The token ({@code X-Auth-Request-Access-Token}) is the single source of
 * truth for identity: {@code sub}, {@code preferred_username}, {@code email},
 * names, and the role claims all come from its claims. Does nothing on
 * requests that lack the token (in-cluster API traffic) — the
 * BearerTokenFilter handles those.
 *
 * Trusting the forwarded token without re-validating its signature is only
 * safe because Traefik's ForwardAuth middleware funnels all browser ingress
 * traffic through oauth2-proxy first. In-cluster traffic bypasses Traefik via
 * Kubernetes service discovery and therefore never carries this header.
 *
 * Fail-closed: a present-but-unparseable token returns 401, and a parseable
 * token that lacks the required role returns 403. See
 * docs/internal/xnat-auth-header-trust-token-only-refactor.md for the
 * rationale behind dropping the former User/Email/Groups header fallbacks.
 */
@Slf4j
@Component
public class HeaderTrustFilter extends OncePerRequestFilter {

    private final ScoutAuthProperties properties;
    private final UserProvisioningService provisioningService;

    public HeaderTrustFilter(final ScoutAuthProperties properties,
                             final UserProvisioningService provisioningService) {
        this.properties = properties;
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

        // The forwarded access token is the single source of truth for browser-
        // path identity. No signature validation here — oauth2-proxy is the
        // trust boundary, not the JWT itself. Fail closed if it won't parse.
        final JWTClaimsSet claims;
        try {
            final JWT jwt = JWTParser.parse(accessToken);
            claims = jwt.getJWTClaimsSet();
        } catch (Exception e) {
            log.warn("HeaderTrustFilter rejecting request: forwarded access token in {} is unparseable ({})",
                    properties.getAccessTokenHeader(), e.getMessage());
            SecurityContextHolder.clearContext();
            response.sendError(HttpServletResponse.SC_UNAUTHORIZED, "Scout auth: invalid access token");
            return;
        }

        final String sub = claims.getSubject();
        final String preferredUsername = claimAsString(claims, "preferred_username");
        final String email = claimAsString(claims, "email");
        final String firstName = claimAsString(claims, "given_name");
        final String lastName = claimAsString(claims, "family_name");

        // The xnat-access gate is enforced solely from the token's role claims:
        // client roles in resource_access.<client>.roles, plus realm roles in
        // realm_access.roles. No group-name inference fallback.
        final List<String> roles = new ArrayList<>();
        roles.addAll(extractClientRoles(claims, properties.getClientId()));
        roles.addAll(extractRealmRoles(claims));
        log.info("HeaderTrustFilter saw roles {} in access token for sub '{}'", roles, sub);

        if (!roles.contains(properties.getRequiredRole())) {
            log.warn("HeaderTrustFilter rejecting request: access token for sub '{}' lacks required role '{}' (saw {})",
                    sub, properties.getRequiredRole(), roles);
            SecurityContextHolder.clearContext();
            response.sendError(HttpServletResponse.SC_FORBIDDEN, "Scout auth: missing required role");
            return;
        }

        final ScoutIdentity identity = new ScoutIdentity(
                sub, preferredUsername, email, firstName, lastName, Collections.emptyList(), roles);
        log.debug("HeaderTrustFilter authenticating {}", identity);

        try {
            final UserI user = provisioningService.provision(identity);
            final ScoutAuthenticationToken token = new ScoutAuthenticationToken(user, "keycloak");
            SecurityContextHolder.getContext().setAuthentication(token);
            // Mirror XDAT.loginUser: populate the session-scoped UserHelper so
            // Velocity-rendered pages (eg. project pages) see a usable
            // permission model. Without this, freshly-authenticated browser
            // sessions render the "Security Warning: not granted access"
            // fallback even when DB-side permissions are correct.
            UserHelper.setUserHelper(request, user);
        } catch (Exception e) {
            log.warn("HeaderTrustFilter rejected request from {}: {}", preferredUsername, e.getMessage());
            SecurityContextHolder.clearContext();
            response.sendError(HttpServletResponse.SC_FORBIDDEN, "Scout auth: " + e.getMessage());
            return;
        }

        chain.doFilter(request, response);
    }

    private static String claimAsString(final JWTClaimsSet claims, final String name) {
        try {
            return claims.getStringClaim(name);
        } catch (java.text.ParseException e) {
            return null;
        }
    }

    @SuppressWarnings("unchecked")
    static List<String> extractClientRoles(JWTClaimsSet claims, String clientId) {
        try {
            Map<String, Object> resourceAccess = (Map<String, Object>) claims.getClaim("resource_access");
            if (resourceAccess == null) return Collections.emptyList();
            Map<String, Object> client = (Map<String, Object>) resourceAccess.get(clientId);
            if (client == null) return Collections.emptyList();
            List<String> roles = (List<String>) client.get("roles");
            return roles != null ? roles : Collections.<String>emptyList();
        } catch (ClassCastException e) {
            return Collections.emptyList();
        }
    }

    @SuppressWarnings("unchecked")
    static List<String> extractRealmRoles(JWTClaimsSet claims) {
        try {
            Map<String, Object> realmAccess = (Map<String, Object>) claims.getClaim("realm_access");
            if (realmAccess == null) return Collections.emptyList();
            List<String> roles = (List<String>) realmAccess.get("roles");
            return roles != null ? roles : Collections.<String>emptyList();
        } catch (ClassCastException e) {
            return Collections.emptyList();
        }
    }
}
