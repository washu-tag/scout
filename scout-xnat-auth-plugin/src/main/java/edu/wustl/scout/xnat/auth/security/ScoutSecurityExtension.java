package edu.wustl.scout.xnat.auth.security;

import edu.wustl.scout.xnat.auth.ScoutAuthMethod;
import lombok.extern.slf4j.Slf4j;
import org.nrg.xnat.security.BaseXnatSecurityExtension;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.stereotype.Component;

/**
 * XNAT plugin-discovery seam. Inserts both filters before the default
 * UsernamePasswordAuthenticationFilter so we run after session lookup (so a
 * populated JSESSIONID short-circuits past us via Spring's session-cached
 * Authentication) but before XNAT's stock form-login machinery.
 *
 * Per plan §3.2: no path-based RequestMatcher scoping; both filters run on
 * every secured request and short-circuit internally based on populated
 * SecurityContext + presence of their respective input.
 */
@Slf4j
@Component
public class ScoutSecurityExtension extends BaseXnatSecurityExtension {

    private final HeaderTrustFilter headerTrustFilter;
    private final BearerTokenFilter bearerTokenFilter;

    public ScoutSecurityExtension(final HeaderTrustFilter headerTrustFilter,
                                  final BearerTokenFilter bearerTokenFilter) {
        this.headerTrustFilter = headerTrustFilter;
        this.bearerTokenFilter = bearerTokenFilter;
    }

    @Override
    public void configure(final HttpSecurity http) {
        try {
            http.addFilterBefore(headerTrustFilter, UsernamePasswordAuthenticationFilter.class)
                    .addFilterAfter(bearerTokenFilter, HeaderTrustFilter.class);
        } catch (Throwable e) {
            log.error("Failed to configure Scout auth filters", e);
        }
    }

    @Override
    public String getAuthMethod() {
        return ScoutAuthMethod.VALUE;
    }
}
