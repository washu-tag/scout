package edu.wustl.scout.xnat.auth;

import edu.wustl.scout.xnat.auth.security.JwtValidator;
import edu.wustl.scout.xnat.auth.security.TokenExchangeService;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Wires the helpers that aren't pure {@code @Component}s — they need
 * configuration values at construction time, so they're declared as
 * {@code @Bean}s here instead.
 */
@Configuration
public class ScoutAuthConfig {

    @Bean
    public JwtValidator jwtValidator(final ScoutAuthProperties properties) {
        return new JwtValidator(properties.getIssuer(), properties.getJwksUri());
    }

    @Bean
    public TokenExchangeService tokenExchangeService(final ScoutAuthProperties properties) {
        return new TokenExchangeService(properties);
    }
}
