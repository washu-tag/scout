package edu.wustl.scout.xnat.auth.security;

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
 * Phase A stub. Phase B will:
 *   1. Validate the incoming JWT against Keycloak's JWKS (signature, issuer, expiry, role claim).
 *   2. Exchange it for an xnat-audience token via Keycloak's token-exchange V2.
 *   3. Validate the exchanged token (audience + role).
 *   4. Provision/look up the XNAT user and attach the principal.
 *
 * For now: short-circuit on populated SecurityContext or absent Authorization
 * header, otherwise log and pass through (Spring's downstream chain returns 401
 * on protected paths, which is the right default until we implement validation).
 */
@Slf4j
@Component
public class BearerTokenFilter extends OncePerRequestFilter {

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
        if (StringUtils.isBlank(authHeader) || !authHeader.startsWith("Bearer ")) {
            chain.doFilter(request, response);
            return;
        }

        log.debug("BearerTokenFilter: Phase A stub — bearer received but validation/exchange not yet implemented");
        chain.doFilter(request, response);
    }
}
