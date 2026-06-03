package edu.wustl.scout.xnat.auth.service;

import edu.wustl.scout.xnat.auth.ScoutAuthConstants;
import edu.wustl.scout.xnat.auth.ScoutAuthProperties;
import edu.wustl.scout.xnat.auth.model.ScoutIdentity;
import org.junit.Before;
import org.junit.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.MockedStatic;
import org.nrg.xdat.entities.XdatUserAuth;
import org.nrg.xdat.exceptions.UsernameAuthMappingNotFoundException;
import org.nrg.xdat.security.helpers.Users;
import org.nrg.xdat.services.XdatUserAuthService;
import org.nrg.xft.security.UserI;
import org.springframework.security.authentication.AuthenticationServiceException;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Collections;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.fail;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Unit tests for {@link DefaultUserProvisioningService}, with a particular eye
 * on the auth_user/xdat_username consistency between lookup and create: the
 * XdatUserAuth row is keyed on auth_user = the Keycloak sub, while the local
 * XNAT login is "keycloak-&lt;sub&gt;". Lookup and create must agree on auth_user
 * or a provisioned user is never found again on subsequent logins.
 */
public class DefaultUserProvisioningServiceTest {

    private static final String SUB = "f077e17f-1234";
    private static final String REQUIRED_ROLE = "xnat-access";
    private static final String USERNAME_PREFIX = "keycloak";
    private static final String EXPECTED_LOGIN = USERNAME_PREFIX + "-" + SUB;

    private XdatUserAuthService userAuthService;
    private ScoutAuthProperties properties;
    private DefaultUserProvisioningService service;

    @Before
    public void setUp() {
        userAuthService = mock(XdatUserAuthService.class);
        properties = new ScoutAuthProperties();
        ReflectionTestUtils.setField(properties, "requiredRole", REQUIRED_ROLE);
        ReflectionTestUtils.setField(properties, "usernamePrefix", USERNAME_PREFIX);
        service = new DefaultUserProvisioningService(userAuthService, properties);
    }

    private static ScoutIdentity identityWithRole(String role) {
        return new ScoutIdentity(SUB, "alice", "alice@example.org", "Alice", "Anderson",
                Collections.singletonList(role));
    }

    /**
     * Existing-user lookup keys on the Keycloak sub (auth_user), NOT the
     * prefixed local login — matching what createUser stores. This is the
     * round-trip the reviewer flagged.
     */
    @Test
    public void provision_looksUpExistingUserBySubNotPrefixedLogin() throws Exception {
        UserI existing = mock(UserI.class);
        when(existing.isEnabled()).thenReturn(true);
        when(userAuthService.getUserDetailsByNameAndAuth(
                eq(SUB), eq(ScoutAuthConstants.AUTH_METHOD), eq(ScoutAuthConstants.PROVIDER_ID)))
                .thenReturn(existing);

        UserI result = service.provision(identityWithRole(REQUIRED_ROLE));

        assertSame(existing, result);
        // Keyed on the raw sub, never on "keycloak-<sub>".
        verify(userAuthService).getUserDetailsByNameAndAuth(
                eq(SUB), eq(ScoutAuthConstants.AUTH_METHOD), eq(ScoutAuthConstants.PROVIDER_ID));
    }

    /**
     * On first sight, createUser persists an XdatUserAuth whose auth_user is the
     * sub and whose xdat_username is the prefixed local login — the two halves
     * of the lookup/create contract.
     */
    @Test
    public void provision_createsUserWithAuthUserSubAndPrefixedLogin() throws Exception {
        when(userAuthService.getUserDetailsByNameAndAuth(any(), any(), any()))
                .thenThrow(new UsernameAuthMappingNotFoundException("a", "b", "c", "d", "e", "f"));

        UserI created = mock(UserI.class);
        when(created.getLogin()).thenReturn(EXPECTED_LOGIN);
        when(created.isEnabled()).thenReturn(true);

        try (MockedStatic<Users> users = org.mockito.Mockito.mockStatic(Users.class)) {
            users.when(Users::createUser).thenReturn(created);
            users.when(Users::getAdminUser).thenReturn(mock(UserI.class));

            UserI result = service.provision(identityWithRole(REQUIRED_ROLE));
            assertSame(created, result);

            verify(created).setLogin(EXPECTED_LOGIN);

            ArgumentCaptor<XdatUserAuth> authCaptor = ArgumentCaptor.forClass(XdatUserAuth.class);
            users.verify(() -> Users.save(eq(created), any(), authCaptor.capture(), eq(false), any()));
            XdatUserAuth auth = authCaptor.getValue();
            assertEquals(SUB, auth.getAuthUser());
            assertEquals(EXPECTED_LOGIN, auth.getXdatUsername());
            assertEquals(ScoutAuthConstants.AUTH_METHOD, auth.getAuthMethod());
            assertEquals(ScoutAuthConstants.PROVIDER_ID, auth.getAuthMethodId());
        }
    }

    @Test
    public void provision_rejectsNullLookupResult() throws Exception {
        // getUserDetailsByNameAndAuth normally throws
        // UsernameAuthMappingNotFoundException when there is no mapping; if an
        // implementation returns null instead, provision must fail closed with a
        // handled AuthenticationServiceException rather than NPE on isEnabled().
        when(userAuthService.getUserDetailsByNameAndAuth(any(), any(), any())).thenReturn(null);
        try {
            service.provision(identityWithRole(REQUIRED_ROLE));
            fail("expected AuthenticationServiceException for null lookup result");
        } catch (AuthenticationServiceException e) {
            // expected
        }
    }

    @Test
    public void provision_rejectsBlankSub() {
        ScoutIdentity noSub = new ScoutIdentity("", "alice", null, null, null,
                Collections.singletonList(REQUIRED_ROLE));
        try {
            service.provision(noSub);
            fail("expected AuthenticationServiceException");
        } catch (AuthenticationServiceException e) {
            // expected
        }
    }

    @Test
    public void provision_rejectsDisabledExistingUser() throws Exception {
        UserI disabled = mock(UserI.class);
        when(disabled.isEnabled()).thenReturn(false);
        when(userAuthService.getUserDetailsByNameAndAuth(any(), any(), any())).thenReturn(disabled);

        try {
            service.provision(identityWithRole(REQUIRED_ROLE));
            fail("expected AuthenticationServiceException for disabled user");
        } catch (AuthenticationServiceException e) {
            // expected
        }
    }
}
