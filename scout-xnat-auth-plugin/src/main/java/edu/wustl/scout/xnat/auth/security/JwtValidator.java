package edu.wustl.scout.xnat.auth.security;

import com.nimbusds.jwt.JWTClaimsSet;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * Validates Keycloak-issued JWTs against the realm's JWKS. Two validation
 * passes share this contract: the incoming subject token (signature + issuer
 * + expiry only — no aud check, since the incoming audience is some other
 * Scout client) and the exchanged xnat-audience token (signature + issuer +
 * expiry + aud + required client role).
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
     * Validate signature/issuer/expiry plus the {@code azp} claim and a client
     * role. Used for the exchanged-token pass.
     *
     * On {@code aud} vs {@code azp}: Keycloak STX V2 puts the *requester*
     * client in {@code azp} (authorized party) and uses {@code aud} for any
     * *other* audiences — sibling clients that the user can also reach. So
     * the right "this token was issued for xnat" check is {@code azp == xnat},
     * not {@code aud contains xnat} (which is empty, since xnat is the
     * requester).
     *
     * Required role check looks at {@code resource_access.<clientId>.roles},
     * which gates on {@code xnat-access} per the realm template's mapping
     * from {@code scout-user} group to xnat client roles.
     */
    JWTClaimsSet validateExchanged(String jwt,
                                   String expectedAuthorizedParty,
                                   String clientId,
                                   String requiredRole) throws InvalidJwtException;

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

    /**
     * Pull the realm role list out of {@code realm_access.roles}, or empty if
     * the claim is missing/malformed.
     */
    @SuppressWarnings("unchecked")
    static List<String> extractRealmRoles(final JWTClaimsSet claims) {
        try {
            Map<String, Object> realmAccess = (Map<String, Object>) claims.getClaim("realm_access");
            if (realmAccess == null) return Collections.emptyList();
            List<String> roles = (List<String>) realmAccess.get("roles");
            return roles != null ? roles : Collections.<String>emptyList();
        } catch (ClassCastException e) {
            return Collections.emptyList();
        }
    }

    /** All roles a validated token carries: client roles for {@code clientId} + realm roles. */
    static List<String> allRoles(final JWTClaimsSet claims, final String clientId) {
        List<String> roles = new ArrayList<>(extractClientRoles(claims, clientId));
        roles.addAll(extractRealmRoles(claims));
        return roles;
    }

    /** Read a string claim, returning null if absent or not a string. */
    static String claimAsString(final JWTClaimsSet claims, final String name) {
        try {
            return claims.getStringClaim(name);
        } catch (java.text.ParseException e) {
            return null;
        }
    }

    /** Read a claim expected to be a list of strings, or empty if missing/wrong type. */
    @SuppressWarnings("unchecked")
    static List<String> stringListClaim(final JWTClaimsSet claims, final String name) {
        Object value = claims.getClaim(name);
        return (value instanceof List) ? (List<String>) value : Collections.emptyList();
    }

    /** Thrown by {@link #validate} and {@link #validateExchanged}. */
    final class InvalidJwtException extends Exception {
        public InvalidJwtException(final String message) {
            super(message);
        }
        public InvalidJwtException(final String message, final Throwable cause) {
            super(message, cause);
        }
    }
}
