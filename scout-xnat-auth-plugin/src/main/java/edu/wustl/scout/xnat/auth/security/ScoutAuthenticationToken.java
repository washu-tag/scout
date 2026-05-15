package edu.wustl.scout.xnat.auth.security;

import org.nrg.xft.security.UserI;
import org.nrg.xnat.security.tokens.AbstractXnatAuthenticationToken;

/**
 * Authentication token attached to the SecurityContext after either filter
 * succeeds. Extends XNAT's AbstractXnatAuthenticationToken so downstream
 * authorization filters see the XdatUser's authorities (eg. ROLE_USER) and
 * recognize the auth as XNAT-issued rather than treating it as anonymous.
 */
public class ScoutAuthenticationToken extends AbstractXnatAuthenticationToken {

    public ScoutAuthenticationToken(final UserI principal, final String providerId) {
        // The 4-arg superclass ctor calls super.setAuthenticated(true) directly;
        // calling our overridden setAuthenticated(true) here would throw.
        super(providerId, principal, null, principal.getAuthorities());
    }
}
