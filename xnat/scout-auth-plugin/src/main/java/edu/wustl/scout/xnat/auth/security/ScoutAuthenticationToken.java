package edu.wustl.scout.xnat.auth.security;

import org.nrg.xft.security.UserI;
import org.springframework.security.authentication.AbstractAuthenticationToken;
import org.springframework.security.core.GrantedAuthority;

import java.util.Collection;
import java.util.Collections;

/**
 * Authentication token attached to the SecurityContext after either filter
 * succeeds. Mirrors the shape of XNAT's existing tokens enough that downstream
 * code that just calls getPrincipal()/isAuthenticated() works unchanged.
 */
public class ScoutAuthenticationToken extends AbstractAuthenticationToken {

    private final UserI principal;
    private final String source;

    public ScoutAuthenticationToken(UserI principal, String source) {
        super(Collections.<GrantedAuthority>emptyList());
        this.principal = principal;
        this.source = source;
        setAuthenticated(true);
    }

    public ScoutAuthenticationToken(UserI principal, String source,
                                    Collection<? extends GrantedAuthority> authorities) {
        super(authorities);
        this.principal = principal;
        this.source = source;
        setAuthenticated(true);
    }

    @Override
    public Object getCredentials() {
        return "";
    }

    @Override
    public Object getPrincipal() {
        return principal;
    }

    @Override
    public String getName() {
        return principal != null ? principal.getUsername() : null;
    }

    public String getSource() {
        return source;
    }
}
