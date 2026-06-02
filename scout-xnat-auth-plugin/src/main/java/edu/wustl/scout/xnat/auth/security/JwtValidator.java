package edu.wustl.scout.xnat.auth.security;

import com.nimbusds.jwt.JWTClaimsSet;

import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * Validates Keycloak-issued JWTs against the realm's JWKS (signature + issuer
 * + expiry). Audience and role checks are applied by the filters on top of the
 * verified claims, since the two auth paths bind differently: the browser path
 * pins {@code azp} to the oauth2-proxy client, while the bearer path requires
 * {@code aud} to contain the xnat client.
 *
 * <p>The production implementation is {@link DefaultJwtValidator}; this
 * interface exists so consumers depend on the abstraction (matching the
 * XNAT plugin idiom) and so tests can substitute mocks without subclassing
 * concrete Nimbus machinery.
 */
public interface JwtValidator {

    /**
     * Validate signature, issuer, expiry. Throws on any failure. Returns the
     * verified claims set on success.
     */
    JWTClaimsSet validate(String jwt) throws InvalidJwtException;

    /**
     * Pull the role list out of {@code resource_access.<clientId>.roles}, or
     * empty if the claim is missing/malformed. Shared with filters that need
     * to read roles off a token they've already validated.
     */
    @SuppressWarnings("unchecked")
    static List<String> extractClientRoles(final JWTClaimsSet claims, final String clientId) {
        try {
            Map<String, Object> resourceAccess = (Map<String, Object>) claims.getClaim("resource_access");
            if (resourceAccess == null) return Collections.emptyList();
            Map<String, Object> client = (Map<String, Object>) resourceAccess.get(clientId);
            if (client == null) return Collections.emptyList();
            List<String> roles = (List<String>) client.get("roles");
            return roles != null ? roles : Collections.<String>emptyList();
        } catch (ClassCastException e) {
            return Collections.emptyList();
        }
    }

    /** Read a string claim, returning null if absent or not a string. */
    static String claimAsString(final JWTClaimsSet claims, final String name) {
        try {
            return claims.getStringClaim(name);
        } catch (java.text.ParseException e) {
            return null;
        }
    }

    /** Thrown by {@link #validate}. */
    final class InvalidJwtException extends Exception {
        public InvalidJwtException(final String message) {
            super(message);
        }
        public InvalidJwtException(final String message, final Throwable cause) {
            super(message, cause);
        }
    }
}
