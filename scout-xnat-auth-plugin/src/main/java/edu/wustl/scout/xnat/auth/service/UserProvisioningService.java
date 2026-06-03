package edu.wustl.scout.xnat.auth.service;

import edu.wustl.scout.xnat.auth.model.ScoutIdentity;
import org.nrg.xft.security.UserI;
import org.springframework.security.core.AuthenticationException;

/**
 * Maps a normalized {@link ScoutIdentity} to an XNAT {@link UserI}, creating
 * the XNAT user on first sight.
 *
 * <p>The production implementation is {@link DefaultUserProvisioningService};
 * this interface exists so the auth filters depend on the abstraction
 * (matching the XNAT plugin idiom) and so tests can substitute a mock.
 */
public interface UserProvisioningService {

    /**
     * Resolve an existing XNAT user for the given identity or create one.
     *
     * <p>Authorization (the {@code xnat-access} role gate) is enforced upstream
     * by the auth filters before they call this method, so {@code provision}
     * assumes an already-authorized identity and does not re-check the role.
     *
     * <p>Failure contract: signals failure with {@link AuthenticationException}
     * (bad/blank identity, disabled user, an unexpected lookup result, or a
     * user-creation failure); the shared {@code ScoutAuthSupport.establishSession}
     * helper turns that into a 403.
     */
    UserI provision(ScoutIdentity identity) throws AuthenticationException;
}
