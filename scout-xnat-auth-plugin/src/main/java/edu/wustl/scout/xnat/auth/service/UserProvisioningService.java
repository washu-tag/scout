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
     * Resolve an existing XNAT user for the given identity or create one. The
     * required-role gate is enforced inside; callers should treat any
     * {@link AuthenticationException} as a 403 outcome.
     */
    UserI provision(ScoutIdentity identity) throws AuthenticationException;
}
