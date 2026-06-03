package edu.wustl.scout.xnat.auth;

import edu.wustl.scout.xnat.auth.security.DefaultJwtValidator;
import edu.wustl.scout.xnat.auth.security.JwtValidator;
import org.apache.commons.lang3.StringUtils;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Wires the helpers that aren't pure {@code @Component}s — they need
 * configuration values at construction time, so they're declared as
 * {@code @Bean}s here instead. Bean methods return the published interfaces
 * so consumers depend on abstractions; the concrete {@code Default*} impls
 * are constructed here.
 */
@Configuration
public class ScoutAuthConfig {

    @Bean
    public JwtValidator jwtValidator(final ScoutAuthProperties properties) {
        // Fail fast with an actionable message rather than letting
        // DefaultJwtValidator's constructor throw a bare "issuer must not be
        // empty" buried in a BeanCreationException. An auth plugin with no
        // validator config must not boot — XNAT startup aborts here until the
        // operator sets these in xnat-conf.properties.
        if (StringUtils.isBlank(properties.getIssuer()) || StringUtils.isBlank(properties.getJwksUri())) {
            throw new IllegalStateException(
                    "Scout auth plugin is misconfigured: scout.keycloak.issuer and "
                            + "scout.keycloak.jwks_uri must both be set in xnat-conf.properties "
                            + "(saw issuer='" + properties.getIssuer()
                            + "', jwks_uri='" + properties.getJwksUri() + "').");
        }
        return new DefaultJwtValidator(properties.getIssuer(), properties.getJwksUri());
    }
}
