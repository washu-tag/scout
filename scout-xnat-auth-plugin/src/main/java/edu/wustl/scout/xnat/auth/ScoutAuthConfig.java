package edu.wustl.scout.xnat.auth;

import edu.wustl.scout.xnat.auth.security.DefaultJwtValidator;
import edu.wustl.scout.xnat.auth.security.DefaultTokenExchangeService;
import edu.wustl.scout.xnat.auth.security.JwtValidator;
import edu.wustl.scout.xnat.auth.security.TokenExchangeService;
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
        return new DefaultJwtValidator(properties.getIssuer(), properties.getJwksUri());
    }

    @Bean
    public TokenExchangeService tokenExchangeService(final ScoutAuthProperties properties) {
        return new DefaultTokenExchangeService(properties);
    }
}
