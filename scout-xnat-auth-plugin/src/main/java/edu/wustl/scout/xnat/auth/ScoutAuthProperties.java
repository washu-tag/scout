package edu.wustl.scout.xnat.auth;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * Plugin configuration, sourced from XNAT's properties file (xnat-conf.properties)
 * and environment overrides. Defaults match Scout's deployment conventions; per-env
 * values come from the xnat-scout-auth ConfigMap mounted into the XNAT pod.
 */
@Component
public class ScoutAuthProperties {

    @Value("${scout.keycloak.issuer:}")
    private String issuer;

    @Value("${scout.keycloak.jwks_uri:}")
    private String jwksUri;

    @Value("${scout.keycloak.token_uri:}")
    private String tokenUri;

    @Value("${scout.keycloak.client_id:xnat}")
    private String clientId;

    @Value("${scout.keycloak.client_secret:}")
    private String clientSecret;

    @Value("${scout.keycloak.required_role:xnat-access}")
    private String requiredRole;

    @Value("${scout.keycloak.oauth2_proxy_client_id:oauth2-proxy}")
    private String oauth2ProxyClientId;

    @Value("${scout.headers.access_token_header:X-Auth-Request-Access-Token}")
    private String accessTokenHeader;

    // Username = "<prefix>-<sub>". The default coincidentally matches
    // ScoutAuthConstants.PROVIDER_ID ("keycloak") but is conceptually
    // independent — they are not linked, so don't collapse them into one value.
    @Value("${scout.username_prefix:keycloak}")
    private String usernamePrefix;

    public String getIssuer() { return issuer; }
    public String getJwksUri() { return jwksUri; }
    public String getTokenUri() { return tokenUri; }
    public String getClientId() { return clientId; }
    public String getClientSecret() { return clientSecret; }
    public String getRequiredRole() { return requiredRole; }
    public String getOauth2ProxyClientId() { return oauth2ProxyClientId; }
    public String getAccessTokenHeader() { return accessTokenHeader; }
    public String getUsernamePrefix() { return usernamePrefix; }
}
