package edu.wustl.scout.xnat.auth;

import org.nrg.xdat.services.XdatUserAuthService;

/**
 * Single source of truth for the {@code XdatUserAuth.auth_method} value Scout
 * writes and advertises. Scout identities are Keycloak (OIDC) identities
 * forwarded by OAuth2 Proxy, so the value is {@link XdatUserAuthService#OPENID}.
 *
 * <p>Both the security extension (which advertises the method to XNAT at
 * startup) and the provisioning service (which writes it onto new
 * {@code XdatUserAuth} rows) must agree, so they both import this constant
 * rather than referencing {@code XdatUserAuthService.OPENID} independently.
 */
public final class ScoutAuthMethod {

    public static final String VALUE = XdatUserAuthService.OPENID;

    private ScoutAuthMethod() {
    }
}
