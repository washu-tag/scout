package edu.wustl.scout.xnat.auth.security;

import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.RSASSASigner;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jose.jwk.source.ImmutableJWKSet;
import com.nimbusds.jose.jwk.source.JWKSource;
import com.nimbusds.jose.proc.SecurityContext;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import org.junit.Before;
import org.junit.Test;

import java.security.interfaces.RSAPrivateKey;
import java.security.interfaces.RSAPublicKey;
import java.util.Arrays;
import java.util.Collections;
import java.util.Date;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

public class JwtValidatorTest {

    private static final String ISSUER = "https://kc.example/realms/scout";

    private RSAKey signingKey;
    private RSAPrivateKey privateKey;
    private JWKSource<SecurityContext> jwkSource;
    private JwtValidator validator;

    @Before
    public void setUp() throws Exception {
        signingKey = new RSAKey.Builder(rsaPublicKey()).privateKey(rsaPrivateKey())
                .keyID("test-kid")
                .algorithm(JWSAlgorithm.RS256)
                .build();
        privateKey = (RSAPrivateKey) signingKey.toRSAPrivateKey();
        jwkSource = new ImmutableJWKSet<>(new JWKSet(signingKey.toPublicJWK()));
        validator = new DefaultJwtValidator(ISSUER, jwkSource);
    }

    @Test
    public void validate_acceptsHappyPathToken() throws Exception {
        String jwt = signToken(claims().subject("user-1").build());
        JWTClaimsSet result = validator.validate(jwt);
        assertEquals("user-1", result.getSubject());
        assertEquals(ISSUER, result.getIssuer());
    }

    @Test
    public void validate_rejectsExpiredToken() throws Exception {
        long now = System.currentTimeMillis();
        String jwt = signToken(claims()
                .expirationTime(new Date(now - 60_000))
                .issueTime(new Date(now - 120_000))
                .subject("user-1")
                .build());
        try {
            validator.validate(jwt);
            fail("expected InvalidJwtException for expired token");
        } catch (JwtValidator.InvalidJwtException e) {
            assertTrue(e.getMessage().toLowerCase().contains("expired")
                    || e.getMessage().toLowerCase().contains("claim"));
        }
    }

    @Test
    public void validate_rejectsWrongIssuer() throws Exception {
        JWTClaimsSet wrong = new JWTClaimsSet.Builder()
                .issuer("https://attacker.example/realms/scout")
                .subject("user-1")
                .expirationTime(new Date(System.currentTimeMillis() + 60_000))
                .issueTime(new Date())
                .jwtID(UUID.randomUUID().toString())
                .build();
        String jwt = signToken(wrong);
        try {
            validator.validate(jwt);
            fail("expected InvalidJwtException for wrong issuer");
        } catch (JwtValidator.InvalidJwtException e) {
            // ok
        }
    }

    @Test
    public void validate_rejectsBadSignature() throws Exception {
        String jwt = signToken(claims().subject("user-1").build());
        // Tamper the last segment.
        String tampered = jwt.substring(0, jwt.length() - 4) + "AAAA";
        try {
            validator.validate(tampered);
            fail("expected InvalidJwtException for tampered signature");
        } catch (JwtValidator.InvalidJwtException e) {
            // ok
        }
    }

    @Test
    public void validate_rejectsTokenSignedByDifferentKey() throws Exception {
        // Sign with a key the validator doesn't know about.
        RSAKey rogueKey = new RSAKey.Builder(rsaPublicKey()).privateKey(rsaPrivateKey())
                .keyID("rogue-kid")
                .algorithm(JWSAlgorithm.RS256)
                .build();
        SignedJWT signed = new SignedJWT(
                new JWSHeader.Builder(JWSAlgorithm.RS256).keyID(rogueKey.getKeyID()).build(),
                claims().subject("user-1").build());
        signed.sign(new RSASSASigner((RSAPrivateKey) rogueKey.toRSAPrivateKey()));
        try {
            validator.validate(signed.serialize());
            fail("expected InvalidJwtException for unknown signing key");
        } catch (JwtValidator.InvalidJwtException e) {
            // ok
        }
    }

    @Test
    public void validateExchanged_acceptsHappyPath() throws Exception {
        String jwt = signToken(claims()
                .subject("user-1")
                .audience("account")
                .claim("azp", "xnat")
                .claim("resource_access", clientRoles("xnat", "xnat-access"))
                .build());
        JWTClaimsSet result = validator.validateExchanged(jwt, "xnat", "xnat", "xnat-access");
        assertEquals("user-1", result.getSubject());
    }

    @Test
    public void validateExchanged_rejectsWrongAuthorizedParty() throws Exception {
        String jwt = signToken(claims()
                .subject("user-1")
                .claim("azp", "not-xnat")
                .claim("resource_access", clientRoles("xnat", "xnat-access"))
                .build());
        try {
            validator.validateExchanged(jwt, "xnat", "xnat", "xnat-access");
            fail("expected InvalidJwtException for wrong azp");
        } catch (JwtValidator.InvalidJwtException e) {
            assertTrue(e.getMessage().contains("azp"));
        }
    }

    @Test
    public void validateExchanged_rejectsMissingAuthorizedParty() throws Exception {
        String jwt = signToken(claims()
                .subject("user-1")
                .claim("resource_access", clientRoles("xnat", "xnat-access"))
                .build());
        try {
            validator.validateExchanged(jwt, "xnat", "xnat", "xnat-access");
            fail("expected InvalidJwtException for missing azp");
        } catch (JwtValidator.InvalidJwtException e) {
            assertTrue(e.getMessage().contains("azp"));
        }
    }

    @Test
    public void validateExchanged_rejectsMissingRole() throws Exception {
        String jwt = signToken(claims()
                .subject("user-1")
                .claim("azp", "xnat")
                .claim("resource_access", clientRoles("xnat", "xnat-user"))  // wrong role
                .build());
        try {
            validator.validateExchanged(jwt, "xnat", "xnat", "xnat-access");
            fail("expected InvalidJwtException for missing role");
        } catch (JwtValidator.InvalidJwtException e) {
            assertTrue(e.getMessage().contains("xnat-access"));
        }
    }

    @Test
    public void validateExchanged_rejectsMissingResourceAccess() throws Exception {
        String jwt = signToken(claims()
                .subject("user-1")
                .claim("azp", "xnat")
                .build());
        try {
            validator.validateExchanged(jwt, "xnat", "xnat", "xnat-access");
            fail("expected InvalidJwtException for missing resource_access");
        } catch (JwtValidator.InvalidJwtException e) {
            assertTrue(e.getMessage().contains("xnat-access"));
        }
    }

    @Test
    public void extractClientRoles_returnsEmptyOnMalformedClaim() {
        JWTClaimsSet broken = new JWTClaimsSet.Builder()
                .claim("resource_access", "not-a-map")
                .build();
        assertTrue(JwtValidator.extractClientRoles(broken, "xnat").isEmpty());
    }

    @Test
    public void stringListClaim_returnsList() {
        JWTClaimsSet claims = new JWTClaimsSet.Builder()
                .claim("groups", Arrays.asList("scout-user", "scout-admin"))
                .build();
        assertEquals(Arrays.asList("scout-user", "scout-admin"),
                JwtValidator.stringListClaim(claims, "groups"));
    }

    @Test
    public void stringListClaim_returnsEmptyOnMissingClaim() {
        JWTClaimsSet claims = new JWTClaimsSet.Builder().subject("user-1").build();
        assertTrue(JwtValidator.stringListClaim(claims, "groups").isEmpty());
    }

    @Test
    public void stringListClaim_returnsEmptyOnNonListClaim() {
        JWTClaimsSet claims = new JWTClaimsSet.Builder()
                .claim("groups", "not-a-list")
                .build();
        assertTrue(JwtValidator.stringListClaim(claims, "groups").isEmpty());
    }

    @Test
    public void claimAsString_returnsNullForMissingClaim() {
        JWTClaimsSet claims = new JWTClaimsSet.Builder().subject("user-1").build();
        org.junit.Assert.assertNull(JwtValidator.claimAsString(claims, "preferred_username"));
    }

    private JWTClaimsSet.Builder claims() {
        long now = System.currentTimeMillis();
        return new JWTClaimsSet.Builder()
                .issuer(ISSUER)
                .issueTime(new Date(now - 1000))
                .expirationTime(new Date(now + 60_000))
                .jwtID(UUID.randomUUID().toString());
    }

    private String signToken(JWTClaimsSet payload) throws JOSEException {
        SignedJWT signed = new SignedJWT(
                new JWSHeader.Builder(JWSAlgorithm.RS256).keyID(signingKey.getKeyID()).build(),
                payload);
        signed.sign(new RSASSASigner(privateKey));
        return signed.serialize();
    }

    private static Map<String, Object> clientRoles(String clientId, String... roles) {
        Map<String, Object> resourceAccess = new HashMap<>();
        Map<String, Object> client = new HashMap<>();
        client.put("roles", Arrays.asList(roles));
        resourceAccess.put(clientId, client);
        return resourceAccess;
    }

    private static RSAPublicKey rsaPublicKey() throws Exception {
        return (RSAPublicKey) keyPair().getPublic();
    }

    private static RSAPrivateKey rsaPrivateKey() throws Exception {
        return (RSAPrivateKey) keyPair().getPrivate();
    }

    // Cache a single keypair across the test class so all helpers share it.
    private static java.security.KeyPair cachedKeyPair;

    private static synchronized java.security.KeyPair keyPair() throws Exception {
        if (cachedKeyPair == null) {
            java.security.KeyPairGenerator gen = java.security.KeyPairGenerator.getInstance("RSA");
            gen.initialize(2048);
            cachedKeyPair = gen.generateKeyPair();
        }
        return cachedKeyPair;
    }
}
