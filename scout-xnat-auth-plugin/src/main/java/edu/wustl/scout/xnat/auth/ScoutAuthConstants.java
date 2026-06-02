package edu.wustl.scout.xnat.auth;

import org.nrg.xdat.services.XdatUserAuthService;

/**
 * Single home for the constants Scout's auth components must agree on. These
 * values are written onto {@code XdatUserAuth} rows and advertised to XNAT, so
 * the producers (the security extension, the provisioning service) and the
 * consumers (the auth filters) all reference the constant rather than repeating
 * a literal that could silently drift.
 */
public final class ScoutAuthConstants {

    /**
     * The {@code XdatUserAuth.auth_method} value Scout writes and advertises.
     * Scout identities are Keycloak (OIDC) identities forwarded by OAuth2
     * Proxy, so the value is {@link XdatUserAuthService#OPENID}. The security
     * extension advertises this to XNAT at startup and the provisioning service
     * writes it onto new {@code XdatUserAuth} rows, so they must agree.
     */
    public static final String AUTH_METHOD = XdatUserAuthService.OPENID;

    /** Provider id written onto XdatUserAuth rows and ScoutAuthenticationTokens. */
    public static final String PROVIDER_ID = "keycloak";

    private ScoutAuthConstants() {
    }
}
