package edu.wustl.scout.xnat.auth.security;

import com.nimbusds.jwt.JWT;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.JWTParser;
import edu.wustl.scout.xnat.auth.ScoutAuthProperties;
import edu.wustl.scout.xnat.auth.model.ScoutIdentity;
import edu.wustl.scout.xnat.auth.service.UserProvisioningService;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
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
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * Trusts identity headers set by oauth2-proxy on browser-path traffic. Does
 * nothing on requests that lack the headers (in-cluster API traffic) — the
 * BearerTokenFilter handles those.
 *
 * Trusting headers is only safe because Traefik's ForwardAuth middleware
 * funnels all browser ingress traffic through oauth2-proxy first. In-cluster
 * traffic bypasses Traefik via Kubernetes service discovery and therefore
 * never carries these headers.
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

        final String preferredUsername = request.getHeader(properties.getUserHeader());
        if (StringUtils.isBlank(preferredUsername)) {
            // Not an oauth2-proxy request; leave the chain alone.
            chain.doFilter(request, response);
            return;
        }

        final String email = request.getHeader(properties.getEmailHeader());
        final String groupsHeader = request.getHeader(properties.getGroupsHeader());
        final String accessToken = request.getHeader(properties.getAccessTokenHeader());

        final List<String> groups = parseCommaList(groupsHeader);
        final List<String> roles = new ArrayList<>();

        // Browser-path identity comes from the oauth2-proxy session. The user's
        // client roles on the xnat client live in resource_access.<client>.roles
        // inside the access token; we need to pull them out to enforce the
        // xnat-access gate. No signature validation here — oauth2-proxy is the
        // trust boundary, not the JWT itself.
        String sub = null;
        String firstName = null;
        String lastName = null;
        if (StringUtils.isNotBlank(accessToken)) {
            try {
                final JWT jwt = JWTParser.parse(accessToken);
                final JWTClaimsSet claims = jwt.getJWTClaimsSet();
                sub = claims.getSubject();
                firstName = (String) claims.getClaim("given_name");
                lastName = (String) claims.getClaim("family_name");
                roles.addAll(extractClientRoles(claims, properties.getClientId()));
                roles.addAll(extractRealmRoles(claims));
            } catch (Exception e) {
                log.warn("failed to parse forwarded access token; falling back to header-only identity", e);
            }
        }
        if (StringUtils.isBlank(sub)) {
            // Fallback when oauth2-proxy didn't forward the access token (or it
            // was unparseable). Use preferred_username as the stable id; if the
            // user's preferred_username changes in Keycloak, they'll show up as
            // a new XNAT user — log noisily so it's findable.
            log.info("no parseable access token in {}; using preferred_username '{}' as sub fallback",
                    properties.getAccessTokenHeader(), preferredUsername);
            sub = preferredUsername;
        }

        // Defense-in-depth role check: oauth2-proxy already enforces
        // scout-user / scout-admin via the oauth2-proxy-user role gate, both of
        // which transitively grant xnat-access. If the access token had the
        // role claim we'll see it directly; otherwise infer from groups.
        if (!roles.contains(properties.getRequiredRole())) {
            if (groups.contains("scout-user") || groups.contains("scout-admin")
                    || groups.contains("/scout-user") || groups.contains("/scout-admin")) {
                roles.add(properties.getRequiredRole());
            }
        }

        final ScoutIdentity identity = new ScoutIdentity(
                sub, preferredUsername, email, firstName, lastName, groups, roles);
        log.debug("HeaderTrustFilter authenticating {}", identity);

        try {
            final UserI user = provisioningService.provision(identity);
            final ScoutAuthenticationToken token = new ScoutAuthenticationToken(user, "header");
            SecurityContextHolder.getContext().setAuthentication(token);
        } catch (Exception e) {
            log.warn("HeaderTrustFilter rejected request from {}: {}", preferredUsername, e.getMessage());
            SecurityContextHolder.clearContext();
            response.sendError(HttpServletResponse.SC_FORBIDDEN, "Scout auth: " + e.getMessage());
            return;
        }

        chain.doFilter(request, response);
    }

    static List<String> parseCommaList(String value) {
        if (StringUtils.isBlank(value)) {
            return Collections.emptyList();
        }
        return Arrays.asList(value.split("\\s*,\\s*"));
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
