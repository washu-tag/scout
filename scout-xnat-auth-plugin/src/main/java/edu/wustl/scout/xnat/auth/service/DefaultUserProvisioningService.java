package edu.wustl.scout.xnat.auth.service;

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
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.AuthenticationException;
import org.springframework.security.authentication.AuthenticationServiceException;
import org.springframework.stereotype.Service;

/**
 * Default {@link UserProvisioningService} that backs onto XNAT's
 * {@link XdatUserAuthService}. Encodes the {@code keycloak-<sub>} username
 * pattern carried over from the previous POC so existing dev03 users stay
 * re-usable if we choose to keep them.
 *
 * <p>Role gate: rejects with 403 unless the identity carries the configured
 * required role (default {@code xnat-access}).
 */
@Slf4j
@Service
public class DefaultUserProvisioningService implements UserProvisioningService {

    private final XdatUserAuthService userAuthService;
    private final ScoutAuthProperties properties;
    private final String authMethod = XdatUserAuthService.OPENID;
    private final String providerId = "keycloak";

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
        if (!identity.hasRole(properties.getRequiredRole())) {
            log.warn("user {} (sub={}) lacks required role {}; rejecting",
                    identity.getPreferredUsername(), identity.getSub(), properties.getRequiredRole());
            throw new AccessDeniedException("user lacks " + properties.getRequiredRole() + " role");
        }

        final String xnatUsername = buildUsername(identity.getSub());
        UserI user;
        try {
            user = userAuthService.getUserDetailsByNameAndAuth(xnatUsername, authMethod, providerId);
        } catch (UsernameAuthMappingNotFoundException e) {
            user = createUser(xnatUsername, identity);
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
