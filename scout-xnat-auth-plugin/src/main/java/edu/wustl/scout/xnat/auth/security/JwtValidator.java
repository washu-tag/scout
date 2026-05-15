package edu.wustl.scout.xnat.auth.security;

import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.proc.BadJOSEException;
import com.nimbusds.jose.jwk.source.JWKSource;
import com.nimbusds.jose.jwk.source.RemoteJWKSet;
import com.nimbusds.jose.proc.JWSKeySelector;
import com.nimbusds.jose.proc.JWSVerificationKeySelector;
import com.nimbusds.jose.proc.SecurityContext;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import com.nimbusds.jwt.proc.ConfigurableJWTProcessor;
import com.nimbusds.jwt.proc.DefaultJWTClaimsVerifier;
import com.nimbusds.jwt.proc.DefaultJWTProcessor;

import java.net.MalformedURLException;
import java.net.URL;
import java.text.ParseException;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Validates Keycloak-issued JWTs against the realm's JWKS. Two validation
 * passes share this class: the incoming subject token (signature + issuer +
 * expiry only — no aud check, since the incoming audience is some other
 * Scout client) and the exchanged xnat-audience token (signature + issuer
 * + expiry + aud + required client role).
 *
 * Nimbus's RemoteJWKSet caches JWKS responses with sane defaults (5-minute
 * cache, on-miss refresh, 30-second connect/read timeouts). We don't tune
 * those.
 */
public class JwtValidator {

    private final ConfigurableJWTProcessor<SecurityContext> processor;
    private final String issuer;

    public JwtValidator(final String issuer, final String jwksUri) {
        if (issuer == null || issuer.isEmpty()) {
            throw new IllegalArgumentException("issuer must not be empty");
        }
        if (jwksUri == null || jwksUri.isEmpty()) {
            throw new IllegalArgumentException("jwksUri must not be empty");
        }
        this.issuer = issuer;
        this.processor = buildProcessor(jwksUri);
    }

    /**
     * Visible for testing; allows injecting a stub JWKSource backed by an
     * in-process JWKS server.
     */
    JwtValidator(final String issuer, final JWKSource<SecurityContext> jwkSource) {
        this.issuer = issuer;
        this.processor = buildProcessor(jwkSource);
    }

    /**
     * Validate signature, issuer, expiry. Throws on any failure. Returns the
     * verified claims set on success.
     */
    public JWTClaimsSet validate(final String jwt) throws InvalidJwtException {
        try {
            SignedJWT parsed = SignedJWT.parse(jwt);
            return processor.process(parsed, null);
        } catch (ParseException e) {
            throw new InvalidJwtException("malformed JWT", e);
        } catch (JOSEException e) {
            throw new InvalidJwtException("JWS verification failed", e);
        } catch (BadJOSEException e) {
            // BadJWTException (claim issues) extends BadJOSEException; catching the
            // parent covers both signature-verification rejection and claim-verifier
            // rejection. Nimbus's messages already encode the reason.
            throw new InvalidJwtException("JWT verification failed: " + e.getMessage(), e);
        }
    }

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
    public JWTClaimsSet validateExchanged(final String jwt,
                                          final String expectedAuthorizedParty,
                                          final String clientId,
                                          final String requiredRole)
            throws InvalidJwtException {
        JWTClaimsSet claims = validate(jwt);
        String azp = null;
        try {
            azp = claims.getStringClaim("azp");
        } catch (java.text.ParseException e) {
            // fall through; azp will be null and the check below fails
        }
        if (!expectedAuthorizedParty.equals(azp)) {
            throw new InvalidJwtException(
                    "exchanged token azp '" + azp + "' is not '" + expectedAuthorizedParty + "'");
        }
        List<String> clientRoles = extractClientRoles(claims, clientId);
        if (!clientRoles.contains(requiredRole)) {
            throw new InvalidJwtException(
                    "exchanged token lacks required client role '" + requiredRole + "'");
        }
        return claims;
    }

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

    private ConfigurableJWTProcessor<SecurityContext> buildProcessor(final String jwksUri) {
        try {
            JWKSource<SecurityContext> source = new RemoteJWKSet<>(new URL(jwksUri));
            return buildProcessor(source);
        } catch (MalformedURLException e) {
            throw new IllegalArgumentException("invalid jwksUri: " + jwksUri, e);
        }
    }

    private ConfigurableJWTProcessor<SecurityContext> buildProcessor(final JWKSource<SecurityContext> source) {
        DefaultJWTProcessor<SecurityContext> processor = new DefaultJWTProcessor<>();

        // Keycloak realms use RS256 by default.
        Set<JWSAlgorithm> algs = new HashSet<>();
        algs.add(JWSAlgorithm.RS256);
        algs.add(JWSAlgorithm.RS384);
        algs.add(JWSAlgorithm.RS512);

        JWSKeySelector<SecurityContext> keySelector = new JWSVerificationKeySelector<>(algs, source);
        processor.setJWSKeySelector(keySelector);

        // Nimbus's DefaultJWTClaimsVerifier checks exp/nbf with a default 60s
        // clock skew. Pin issuer; aud and role get checked imperatively above
        // so we can give better error messages and skip aud on the incoming
        // pass.
        Set<String> requiredClaims = new HashSet<>();
        requiredClaims.add("iss");
        requiredClaims.add("exp");
        requiredClaims.add("sub");
        JWTClaimsSet exact = new JWTClaimsSet.Builder().issuer(issuer).build();
        processor.setJWTClaimsSetVerifier(new DefaultJWTClaimsVerifier<>(exact, requiredClaims));

        return processor;
    }

    /** Thrown by {@link #validate} and {@link #validateExchanged}. */
    public static final class InvalidJwtException extends Exception {
        public InvalidJwtException(final String message) {
            super(message);
        }
        public InvalidJwtException(final String message, final Throwable cause) {
            super(message, cause);
        }
    }
}
