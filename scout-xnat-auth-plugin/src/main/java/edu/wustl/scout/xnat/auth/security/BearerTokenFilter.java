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
 * Authenticates in-cluster API callers by:
 *   1. Validating the incoming JWT against Keycloak's JWKS (sig + iss + exp).
 *   2. Exchanging it for an xnat-audience token via Standard Token Exchange V2.
 *   3. Validating the exchanged token (aud = xnat, role = xnat-access).
 *   4. Provisioning/looking up the XNAT user via {@link UserProvisioningService}
 *      and attaching the principal.
 *
 * Short-circuits on populated SecurityContext (so a session cookie skips us)
 * or absent {@code Authorization: Bearer} (so the chain falls through to
 * Spring's default unauthenticated path on browser/empty calls).
 */
@Slf4j
@Component
public class BearerTokenFilter extends OncePerRequestFilter {

    private static final String BEARER_PREFIX = "Bearer ";

    private final ScoutAuthProperties properties;
    private final JwtValidator jwtValidator;
    private final TokenExchangeService tokenExchangeService;
    private final UserProvisioningService provisioningService;

    public BearerTokenFilter(final ScoutAuthProperties properties,
                             final JwtValidator jwtValidator,
                             final TokenExchangeService tokenExchangeService,
                             final UserProvisioningService provisioningService) {
        this.properties = properties;
        this.jwtValidator = jwtValidator;
        this.tokenExchangeService = tokenExchangeService;
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
        final String subjectJwt = authHeader.substring(BEARER_PREFIX.length()).trim();

        try {
            // Step 1: validate signature/iss/exp on the incoming token. The plan
            // explicitly does NOT validate aud here — the incoming token's aud
            // is some other Scout client (eg. jupyterhub).
            jwtValidator.validate(subjectJwt);
        } catch (JwtValidator.InvalidJwtException e) {
            log.info("BearerTokenFilter rejecting request: incoming JWT invalid ({})", e.getMessage());
            response.sendError(HttpServletResponse.SC_UNAUTHORIZED, "invalid bearer token");
            return;
        }

        // Step 2: exchange.
        final String exchangedJwt;
        try {
            exchangedJwt = tokenExchangeService.exchange(subjectJwt);
        } catch (TokenExchangeService.TokenExchangeException e) {
            log.info("BearerTokenFilter rejecting request: token exchange failed ({})", e.getMessage());
            response.sendError(HttpServletResponse.SC_UNAUTHORIZED, "token exchange failed");
            return;
        }

        // Step 3: validate the exchanged token (aud=xnat, role=xnat-access).
        final JWTClaimsSet claims;
        try {
            claims = jwtValidator.validateExchanged(exchangedJwt,
                    properties.getClientId(), properties.getClientId(), properties.getRequiredRole());
        } catch (JwtValidator.InvalidJwtException e) {
            log.warn("BearerTokenFilter rejecting request: exchanged JWT invalid ({})", e.getMessage());
            response.sendError(HttpServletResponse.SC_FORBIDDEN, "exchanged token rejected");
            return;
        }

        // Step 4: provision and attach.
        final ScoutIdentity identity = ScoutAuthSupport.identityFrom(claims, properties.getClientId());
        log.debug("BearerTokenFilter authenticating {}", identity);
        if (!ScoutAuthSupport.establishSession(request, response, provisioningService, identity, log)) {
            return;
        }

        chain.doFilter(request, response);
    }
}
