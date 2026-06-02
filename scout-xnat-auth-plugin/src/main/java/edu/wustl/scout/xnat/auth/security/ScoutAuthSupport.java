package edu.wustl.scout.xnat.auth.security;

import com.nimbusds.jwt.JWTClaimsSet;
import edu.wustl.scout.xnat.auth.ScoutAuthConstants;
import edu.wustl.scout.xnat.auth.model.ScoutIdentity;
import edu.wustl.scout.xnat.auth.service.UserProvisioningService;
import org.nrg.xdat.security.helpers.UserHelper;
import org.nrg.xft.security.UserI;
import org.slf4j.Logger;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.AuthenticationException;
import org.springframework.security.core.context.SecurityContextHolder;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;

/**
 * Post-validation logic shared by {@link HeaderTrustFilter} and
 * {@link BearerTokenFilter}: turn validated claims into a {@link ScoutIdentity},
 * then provision the user, attach the principal, and populate the session
 * UserHelper.
 *
 * <p>Kept as static helpers rather than a shared base filter on purpose — per
 * the refactor plan, the filters' bodies aren't in their final shape until the
 * token-exchange removal, so a base class is premature.
 */
final class ScoutAuthSupport {

    private ScoutAuthSupport() {
    }

    /**
     * Build a {@link ScoutIdentity} from an already-validated token's claims.
     * Groups are read uniformly from the {@code groups} claim; the header-path
     * token simply won't carry it, yielding an empty list.
     */
    static ScoutIdentity identityFrom(final JWTClaimsSet claims, final String clientId) {
        return new ScoutIdentity(
                claims.getSubject(),
                JwtValidator.claimAsString(claims, "preferred_username"),
                JwtValidator.claimAsString(claims, "email"),
                JwtValidator.claimAsString(claims, "given_name"),
                JwtValidator.claimAsString(claims, "family_name"),
                JwtValidator.stringListClaim(claims, "groups"),
                JwtValidator.allRoles(claims, clientId));
    }

    /**
     * Provision the identity, attach the principal to the SecurityContext, and
     * populate the session UserHelper. Returns true on success; on failure
     * clears the context, writes a 403, and returns false (caller should stop).
     *
     * <p>Mirror XDAT.loginUser: populate the session-scoped UserHelper so the
     * Velocity browser path (eg. project pages) sees a usable permission model
     * instead of the "no access" fallback.
     *
     * <p>Catches both exception types {@link UserProvisioningService#provision}
     * can fail with: {@code AuthenticationException} (bad/disabled user, create
     * failure) and {@code AccessDeniedException} (the role gate — a
     * {@code RuntimeException}, not an {@code AuthenticationException}). Both
     * auth paths must turn either into a 403, not leak the {@code AccessDeniedException}
     * as a 500.
     */
    static boolean establishSession(final HttpServletRequest request,
                                    final HttpServletResponse response,
                                    final UserProvisioningService provisioningService,
                                    final ScoutIdentity identity,
                                    final Logger log) throws IOException {
        try {
            final UserI user = provisioningService.provision(identity);
            SecurityContextHolder.getContext()
                    .setAuthentication(new ScoutAuthenticationToken(user, ScoutAuthConstants.PROVIDER_ID));
            UserHelper.setUserHelper(request, user);
            return true;
        } catch (AuthenticationException | AccessDeniedException e) {
            log.warn("Scout auth provisioning failed for {}: {}", identity.getPreferredUsername(), e.getMessage());
            SecurityContextHolder.clearContext();
            // Don't echo provisioning detail to the client — logged above for operators.
            response.sendError(HttpServletResponse.SC_FORBIDDEN, "Scout auth: user provisioning failed");
            return false;
        }
    }
}
