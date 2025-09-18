package edu.washu.tag.keycloak.events;

import static org.keycloak.models.utils.KeycloakModelUtils.runJobInTransaction;

import java.util.Map;
import java.util.Optional;
import org.jboss.logging.Logger;
import org.keycloak.email.EmailException;
import org.keycloak.email.EmailSenderProvider;
import org.keycloak.email.EmailTemplateProvider;
import org.keycloak.events.Event;
import org.keycloak.events.EventListenerProvider;
import org.keycloak.events.EventListenerTransaction;
import org.keycloak.events.EventType;
import org.keycloak.events.admin.AdminEvent;
import org.keycloak.events.admin.OperationType;
import org.keycloak.events.admin.ResourceType;
import org.keycloak.http.HttpRequest;
import org.keycloak.models.KeycloakContext;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.models.RealmModel;
import org.keycloak.models.ThemeManager;
import org.keycloak.models.UserModel;
import org.keycloak.theme.Theme;
import org.keycloak.theme.freemarker.FreeMarkerProvider;

/**
 * Event listener provider to listen for user registration events to send a
 * welcome email to the user and an approval email to admins. It also listens
 * for group membership events to send enabled/disabled emails to users when
 * they are added or removed from the "scout-user" group.
 */
public class UserApprovalEmailEventListenerProvider implements EventListenerProvider {

    private static final Logger log = Logger.getLogger(UserApprovalEmailEventListenerProvider.class);
    private final KeycloakSession session;
    private final EventListenerTransaction tx = new EventListenerTransaction(this::sendEmail, this::sendEmail);
    private final KeycloakSessionFactory sessionFactory;
    private static final String SCOUT_USER_GROUP = "scout-user";

    /**
     * Constructor for UserApprovalEmailEventListenerProvider.
     *
     * @param session The current Keycloak session
     */
    public UserApprovalEmailEventListenerProvider(KeycloakSession session) {
        this.session = session;
        this.session.getTransactionManager().enlistAfterCompletion(tx);
        this.sessionFactory = session.getKeycloakSessionFactory();
    }

    /**
     * Registraion events are user events. Send welcome email to users and
     * approval email to admins.
     */
    @Override
    public void onEvent(Event event) {
        log.debugf("Received event: %s, type: %s, realm: %s, user: %s, details: %s",
                event.getId(), event.getType(), event.getRealmId(), event.getUserId(), event.getDetails());
        if (isUserRegistrationEvent(event)) {
            tx.addEvent(event);
        }
    }

    /**
     * Group membership events are admin events. Check if the user is in the
     * scout-user group and send endabled / disabled emails to the user.
     */
    @Override
    public void onEvent(AdminEvent event, boolean includeRepresentation) {
        log.debugf("Received admin event: %s, type: %s, realm: %s, resourcePath: %s, details: %s, representation: %s",
                event.getId(), event.getOperationType(), event.getRealmId(), event.getResourcePath(), event.getDetails(), event.getRepresentation());
        if (isScoutUserGroupMembershipEvent(event)) {
            tx.addAdminEvent(event, includeRepresentation);
        }
    }

    @Override
    public void close() {
    }

    private boolean isUserRegistrationEvent(Event event) {
        return event.getType() == EventType.REGISTER
                && event.getRealmId() != null
                && event.getUserId() != null;
    }

    private boolean isScoutUserGroupMembershipEvent(AdminEvent event) {
        return event.getResourceType() == ResourceType.GROUP_MEMBERSHIP
                && (event.getOperationType() == OperationType.CREATE || event.getOperationType() == OperationType.DELETE)
                && event.getRealmId() != null
                && event.getRepresentation() != null
                && event.getRepresentation().contains(SCOUT_USER_GROUP);
    }

    /**
     * Send registration event emails.
     */
    private void sendEmail(Event event) {
        runJobInTransaction(sessionFactory, (KeycloakSession sess) -> {
            KeycloakContext context = sess.getContext();
            RealmModel realm = sess.realms().getRealm(event.getRealmId());
            context.setRealm(realm);
            context.setHttpRequest(session.getContext().getHttpRequest());
            UserModel user = sess.users().getUserById(realm, event.getUserId());

            if (!isUserValid(user, event, realm)) {
                return;
            }
            Map<String, Object> bodyAttributes = new java.util.HashMap<>();
            bodyAttributes.put("username", user.getUsername());

            getScoutUrl().ifPresent(url -> bodyAttributes.put("scoutUrl", url));

            sendUserPendingApprovalEmail(sess, realm, user, bodyAttributes);
            sendAdminApprovalEmail(session, realm, bodyAttributes, user);
        });
    }

    /**
     * Send group membership event emails.
     */
    private void sendEmail(AdminEvent event, boolean includeRepresentation) {
        HttpRequest request = session.getContext().getHttpRequest();
        runJobInTransaction(sessionFactory, (KeycloakSession sess) -> {
            KeycloakContext context = sess.getContext();
            RealmModel realm = sess.realms().getRealm(event.getRealmId());
            context.setRealm(realm);
            context.setHttpRequest(request);
            // Not a user event so we need to get the username from the event details
            Map<String, String> details = event.getDetails();
            String username = details.get("username");
            if (username == null) {
                log.warnf("Username not found in event details: %s. Unable to check scout-user group membership.", event.getDetails());
                return;
            }
            UserModel user = sess.users().getUserByUsername(realm, username);
            if (user == null) {
                log.warnf("User %s not found in realm %s. Unable to check scout-user group membership.", username, realm.getName());
                return;
            }

            Map<String, Object> bodyAttributes = new java.util.HashMap<>();
            getScoutUrl().ifPresent(url -> bodyAttributes.put("scoutUrl", url));

            if (event.getOperationType() == OperationType.CREATE) {
                sendUserEnabledEmail(sess, realm, user, bodyAttributes);
            } else if (event.getOperationType() == OperationType.DELETE) {
                sendUserDisabledEmail(sess, realm, user, bodyAttributes);
            }
        });
    }

    private boolean isUserValid(UserModel user, Event event, RealmModel realm) {
        if (user == null) {
            log.warnf("User not found for event: %s, userId: %s, realm: %s", event.getType(), event.getUserId(), realm.getName());
            return false;
        }
        if (user.getEmail() == null || user.getEmail().isEmpty()) {
            log.warnf("User %s does not have an email address set, skipping email notification.", user.getUsername());
            return false;
        }
        log.infof("User found: %s, email: %s. Sending approval emails.", user.getUsername(), user.getEmail());
        return true;
    }

    private void sendUserPendingApprovalEmail(KeycloakSession session, RealmModel realm, UserModel user, Map<String, Object> bodyAttributes) {
        try {
            EmailTemplateProvider emailTemplateProvider = session.getProvider(EmailTemplateProvider.class);
            emailTemplateProvider.setRealm(realm).setUser(user);
            String subject = "Scout Approval Pending";
            String bodyTemplate = "email-user-pending.ftl";
            emailTemplateProvider.send(subject, bodyTemplate, bodyAttributes);
            log.infof("Pending approval email sent to user: %s, email: %s", user.getUsername(), user.getEmail());
        } catch (Exception e) {
            log.errorf("Failed to send pending approval email for user: %s, email: %s", user.getUsername(), user.getEmail(), e);
        }
    }

    private void sendAdminApprovalEmail(KeycloakSession session, RealmModel realm, Map<String, Object> bodyAttributes, UserModel user) {
        try {
            EmailSenderProvider emailSender = session.getProvider(EmailSenderProvider.class);
            FreeMarkerProvider freeMarker = session.getProvider(FreeMarkerProvider.class);
            ThemeManager themeManager = session.getProvider(ThemeManager.class);
            Theme theme = themeManager.getTheme(realm.getEmailTheme(), Theme.Type.EMAIL);
            String subject = "New Scout User Approval Required";
            String plaintextBody = freeMarker.processTemplate(bodyAttributes, "text/email-admin-approval.ftl", theme);
            String htmlBody = freeMarker.processTemplate(bodyAttributes, "html/email-admin-approval.ftl", theme);
            emailSender.send(
                    realm.getSmtpConfig(),
                    realm.getSmtpConfig().get("from"),
                    subject,
                    plaintextBody,
                    htmlBody
            );
            log.infof("Admin approval email sent for user: %s, email: %s to admin email: %s",
                    user.getUsername(), user.getEmail(), realm.getSmtpConfig().get("from"));
        } catch (Exception e) {
            log.errorf("Failed to send admin approval email for user: %s, email: %s, admin email: %s",
                    user.getUsername(), user.getEmail(), realm.getSmtpConfig().get("from"), e);
        }
    }

    private void sendUserEnabledEmail(KeycloakSession session, RealmModel realm, UserModel user, Map<String, Object> bodyAttributes) {
        try {
            EmailTemplateProvider emailTemplateProvider = session.getProvider(EmailTemplateProvider.class);
            emailTemplateProvider.setRealm(realm).setUser(user);
            String subject = "Welcome to Scout";
            String bodyTemplate = "email-user-enabled.ftl";
            emailTemplateProvider.send(subject, bodyTemplate, bodyAttributes);
            log.infof("Approval email sent to user: %s, email: %s", user.getUsername(), user.getEmail());
        } catch (EmailException e) {
            log.errorf("Failed to send user approval email for user: %s, email: %s", user.getUsername(), user.getEmail(), e);
        }
    }

    private void sendUserDisabledEmail(KeycloakSession session, RealmModel realm, UserModel user, Map<String, Object> bodyAttributes) {
        try {
            EmailTemplateProvider emailTemplateProvider = session.getProvider(EmailTemplateProvider.class);
            emailTemplateProvider.setRealm(realm).setUser(user);
            String subject = "Scout Account Disabled";
            String bodyTemplate = "email-user-disabled.ftl";
            emailTemplateProvider.send(subject, bodyTemplate, bodyAttributes);
            log.infof("Disabled email sent to user: %s, email: %s", user.getUsername(), user.getEmail());
        } catch (EmailException e) {
            log.errorf("Failed to send user disabled email for user: %s, email: %s", user.getUsername(), user.getEmail(), e);
        }
    }

    private Optional<String> getScoutUrl() {
        String scoutUrl = System.getenv("KC_SCOUT_URL");
        
        if (scoutUrl == null || scoutUrl.isEmpty()) {
            log.error("KC_SCOUT_URL environment variable is not set. Unable to find Scout URL for emails.");
            return Optional.empty();
        }

        if (!scoutUrl.startsWith("http://") && !scoutUrl.startsWith("https://")) {
            scoutUrl = "https://" + scoutUrl;
        }

        return Optional.of(scoutUrl);
    }

}
