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
     * required-role gate is enforced inside.
     *
     * <p>Failure contract: this method fails with one of two types, and callers
     * must treat <em>both</em> as a 403 outcome:
     * <ul>
     *   <li>{@link AuthenticationException} — bad/blank identity, disabled user,
     *       or a user-creation failure.</li>
     *   <li>{@code org.springframework.security.access.AccessDeniedException} —
     *       the required-role gate. This is a {@code RuntimeException} and
     *       <em>not</em> an {@code AuthenticationException}, so a catch clause
     *       narrowed to {@code AuthenticationException} alone would let it escape
     *       as a 500.</li>
     * </ul>
     * The shared {@code ScoutAuthSupport.establishSession} helper multi-catches
     * both for exactly this reason.
     */
    UserI provision(ScoutIdentity identity) throws AuthenticationException;
}
