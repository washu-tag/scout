package edu.wustl.scout.xnat.auth.security;

/**
 * Calls Keycloak's token endpoint with the Standard Token Exchange V2 grant
 * to swap an incoming Scout-client-issued access token for one whose audience
 * is {@code xnat}.
 *
 * <p>The production implementation is {@link DefaultTokenExchangeService};
 * this interface exists so consumers depend on the abstraction (matching the
 * XNAT plugin idiom) and so tests can substitute a mock.
 */
public interface TokenExchangeService {

    /**
     * Exchange the given subject JWT for an xnat-audience token. Returns the
     * raw exchanged JWT string; the caller is expected to validate it via
     * {@link JwtValidator#validateExchanged}.
     */
    String exchange(String subjectJwt) throws TokenExchangeException;

    /** Thrown by {@link #exchange}. */
    final class TokenExchangeException extends Exception {
        public TokenExchangeException(final String message) {
            super(message);
        }
        public TokenExchangeException(final String message, final Throwable cause) {
            super(message, cause);
        }
    }
}
