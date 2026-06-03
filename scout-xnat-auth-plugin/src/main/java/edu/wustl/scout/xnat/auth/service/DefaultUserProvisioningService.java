package edu.wustl.scout.xnat.auth.service;

import edu.wustl.scout.xnat.auth.ScoutAuthConstants;
import edu.wustl.scout.xnat.auth.ScoutAuthProperties;
import edu.wustl.scout.xnat.auth.model.ScoutIdentity;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
import org.nrg.xdat.entities.XdatUserAuth;
import org.nrg.xdat.security.helpers.Users;
import org.nrg.xdat.services.XdatUserAuthService;
import org.nrg.xft.event.EventDetails;
import org.nrg.xft.event.EventUtils;
import org.nrg.xdat.exceptions.UsernameAuthMappingNotFoundException;
import org.nrg.xft.security.UserI;
import org.springframework.security.core.AuthenticationException;
import org.springframework.security.authentication.AuthenticationServiceException;
import org.springframework.stereotype.Service;

/**
 * Default {@link UserProvisioningService} that backs onto XNAT's
 * {@link XdatUserAuthService}. Encodes the {@code keycloak-<sub>} username
 * pattern carried over from the previous POC so existing dev03 users stay
 * re-usable if we choose to keep them.
 *
 * <p>Assumes an already-authorized identity: the {@code xnat-access} role gate
 * is enforced by the auth filters (the security boundary) before they call
 * {@code provision}, so it is not re-checked here. This service only validates
 * that the identity is usable (non-blank sub, enabled user) and looks up or
 * creates the XNAT user.
 */
@Slf4j
@Service
public class DefaultUserProvisioningService implements UserProvisioningService {

    private final XdatUserAuthService userAuthService;
    private final ScoutAuthProperties properties;
    private final String authMethod = ScoutAuthConstants.AUTH_METHOD;
    private final String providerId = ScoutAuthConstants.PROVIDER_ID;

    public DefaultUserProvisioningService(final XdatUserAuthService userAuthService,
                                          final ScoutAuthProperties properties) {
        this.userAuthService = userAuthService;
        this.properties = properties;
    }

    @Override
    public UserI provision(final ScoutIdentity identity) throws AuthenticationException {
        if (identity == null || StringUtils.isBlank(identity.getSub())) {
            throw new AuthenticationServiceException("ScoutIdentity has no sub");
        }

        // Two distinct identifiers on the XdatUserAuth row:
        //   - auth_user      = the external IdP identity (the Keycloak sub). This
        //                      is the lookup key (getUserByNameAndAuth ->
        //                      findByAuthUsernameAndProvider keys on auth_user).
        //   - xdat_username  = the local XNAT login we mint, "keycloak-<sub>".
        // The lookup must use the sub so it matches what createUser() stores;
        // keying it on xnatUsername would never find the row after first login.
        final String authUser = identity.getSub();
        final String xnatUsername = buildUsername(authUser);
        UserI user;
        try {
            user = userAuthService.getUserDetailsByNameAndAuth(authUser, authMethod, providerId);
        } catch (UsernameAuthMappingNotFoundException e) {
            user = createUser(xnatUsername, identity);
        }
        if (user == null) {
            // The "no existing mapping" case is signalled by
            // UsernameAuthMappingNotFoundException (handled above). A null return
            // is an unexpected lookup state — fail closed with a handled auth
            // error rather than NPE-ing on isEnabled() below (which would escape
            // establishSession's catch and surface as a 500).
            throw new AuthenticationServiceException(
                    "XNAT user lookup returned null for sub " + authUser);
        }
        if (!user.isEnabled()) {
            throw new AuthenticationServiceException("XNAT user " + xnatUsername + " is disabled");
        }
        return user;
    }

    private String buildUsername(final String sub) {
        return properties.getUsernamePrefix() + "-" + sub;
    }

    private UserI createUser(final String xnatUsername, final ScoutIdentity identity)
            throws AuthenticationException {
        log.info("provisioning XNAT user {} for sub {}", xnatUsername, identity.getSub());
        final UserI xdatUser = Users.createUser();
        xdatUser.setLogin(xnatUsername);
        xdatUser.setEmail(StringUtils.defaultString(identity.getEmail(), ""));
        xdatUser.setFirstname(StringUtils.defaultString(identity.getFirstName(), identity.getPreferredUsername()));
        xdatUser.setLastname(StringUtils.defaultString(identity.getLastName(), ""));
        xdatUser.setEnabled(true);
        xdatUser.setVerified(true);

        try {
            final UserI admin = Users.getAdminUser();
            // auth_user = the Keycloak sub (the lookup key in provision());
            // xdat_username = the local login "keycloak-<sub>". These are
            // intentionally different — do not collapse them to the same value.
            final XdatUserAuth auth = new XdatUserAuth(
                    identity.getSub(), authMethod, providerId, xdatUser.getLogin(), true, 0);
            Users.save(xdatUser, admin, auth,
                    false,
                    new EventDetails(
                            EventUtils.CATEGORY.DATA, EventUtils.TYPE.WEB_SERVICE,
                            "Added User", "Auto-provisioned by xnat-scout-auth-plugin",
                            "Created XNAT user " + xnatUsername + " from Keycloak sub " + identity.getSub()));
            xdatUser.setAuthorization(auth);
        } catch (Exception e) {
            log.error("failed to create XNAT user {}", xnatUsername, e);
            throw new AuthenticationServiceException("failed to create XNAT user", e);
        }
        return xdatUser;
    }
}
