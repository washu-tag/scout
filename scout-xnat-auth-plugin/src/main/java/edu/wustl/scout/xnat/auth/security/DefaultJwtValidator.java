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
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Default Nimbus-backed implementation of {@link JwtValidator}.
 *
 * Nimbus's RemoteJWKSet caches JWKS responses with sane defaults (5-minute
 * cache, on-miss refresh, 30-second connect/read timeouts). We don't tune
 * those.
 */
public class DefaultJwtValidator implements JwtValidator {

    private final ConfigurableJWTProcessor<SecurityContext> processor;
    private final String issuer;

    public DefaultJwtValidator(final String issuer, final String jwksUri) {
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
    DefaultJwtValidator(final String issuer, final JWKSource<SecurityContext> jwkSource) {
        this.issuer = issuer;
        this.processor = buildProcessor(jwkSource);
    }

    @Override
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

    @Override
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
        List<String> clientRoles = JwtValidator.extractClientRoles(claims, clientId);
        if (!clientRoles.contains(requiredRole)) {
            throw new InvalidJwtException(
                    "exchanged token lacks required client role '" + requiredRole + "'");
        }
        return claims;
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
}
