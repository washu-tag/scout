package edu.washu.tag.keycloak.events;

import java.io.IOException;
import java.util.Map;

import org.jboss.logging.Logger;
import org.keycloak.email.EmailException;
import org.keycloak.email.EmailTemplateProvider;
import org.keycloak.email.EmailSenderProvider;
import org.keycloak.events.Event;
import org.keycloak.events.EventListenerProvider;
import org.keycloak.events.EventListenerTransaction;
import org.keycloak.events.EventType;
import org.keycloak.events.admin.AdminEvent;
import org.keycloak.events.admin.OperationType;
import org.keycloak.events.admin.ResourceType;
import org.keycloak.http.HttpRequest;
import org.keycloak.models.ClientModel;
import org.keycloak.models.KeycloakContext;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.models.KeycloakSessionTask;
import org.keycloak.models.RealmModel;
import org.keycloak.models.ThemeManager;
import org.keycloak.models.UserModel;
import org.keycloak.theme.FreeMarkerException;
import org.keycloak.theme.Theme;
import org.keycloak.theme.freemarker.FreeMarkerProvider;
import static org.keycloak.models.utils.KeycloakModelUtils.runJobInTransaction;


public class UserApprovalEmailEventListenerProvider implements EventListenerProvider {

    private static final Logger log = Logger.getLogger(UserApprovalEmailEventListenerProvider.class);

    private final KeycloakSession session;
    private final EventListenerTransaction tx = new EventListenerTransaction(this::sendEmail, this::sendEmail);
    private final KeycloakSessionFactory sessionFactory;

    public UserApprovalEmailEventListenerProvider(KeycloakSession session) {
        this.session = session;
        this.session.getTransactionManager().enlistAfterCompletion(tx);
        this.sessionFactory = session.getKeycloakSessionFactory();
    }

    @Override
    public void onEvent(Event event) {
        log.debugf("Received event: %s, type: %s, realm: %s, user: %s",
                event.getId(), event.getType(), event.getRealmId(), event.getUserId());

        if (event.getType() == EventType.REGISTER) {
            if (event.getRealmId() != null && event.getUserId() != null) {
                tx.addEvent(event);
            }
        }
    }

    private void sendEmail(Event event) {
        HttpRequest request = session.getContext().getHttpRequest();

        log.infof("Processing event: %s for user: %s in realm: %s, details: %s",
                event.getType(), event.getUserId(), event.getRealmId(), event.getDetails());

        runJobInTransaction(sessionFactory, (KeycloakSession session1) -> {
            KeycloakContext context = session1.getContext();
            RealmModel realm = session1.realms().getRealm(event.getRealmId());
            context.setRealm(realm);
            Map<String, String> smtpConfig = realm.getSmtpConfig();
            log.infof("SMTP Config: %s", smtpConfig);
            String clientId = event.getClientId();
            if (clientId != null) {
                ClientModel client = realm.getClientByClientId(clientId);
                context.setClient(client);
            }
            context.setHttpRequest(request);
            UserModel user = session1.users().getUserById(realm, event.getUserId());

            // User should be registered by this point, but double-check
            if (user == null) {
                log.warnf("User not found for event: %s, userId: %s, realm: %s", event.getType(), event.getUserId(), realm.getName());
                return;
            }

            // OIDC users should have an email address set, but double-check
            if (user.getEmail() == null || user.getEmail().isEmpty()) {
                log.warnf("User %s does not have an email address set, skipping email notification.", user.getUsername());
                return;
            }

            log.infof("User found: %s, email: %s. Sending approval emails.", user.getUsername(), user.getEmail());

            try {
                // Send pending approval email to user
                EmailTemplateProvider emailTemplateProvider = session1.getProvider(EmailTemplateProvider.class);
                emailTemplateProvider.setRealm(realm).setUser(user);
                String subject = "Scout Approval Pending";
                String bodyTemplate = "email-user-pending.ftl";
                Map<String, Object> bodyAttributes = new java.util.HashMap<>();
                bodyAttributes.put("username", user.getUsername());
                emailTemplateProvider.send(subject, bodyTemplate, bodyAttributes);
                log.infof("Pending approval email sent to user: %s, email: %s", user.getUsername(), user.getEmail());

                // Send admin approval email
                EmailSenderProvider emailSender = session.getProvider(EmailSenderProvider.class);
                FreeMarkerProvider freeMarker = session.getProvider(FreeMarkerProvider.class); 
                ThemeManager themeManager = session.getProvider(ThemeManager.class);
                Theme theme = themeManager.getTheme(realm.getEmailTheme(), Theme.Type.EMAIL);

                String subject1 = "New Scout User Approval Required";
                String plaintextBody = freeMarker.processTemplate(bodyAttributes, "text/email-admin-approval.ftl", theme);
                String htmlBody = freeMarker.processTemplate(bodyAttributes, "html/email-admin-approval.ftl", theme);
                
                emailSender.send(
                    realm.getSmtpConfig(),
                    realm.getSmtpConfig().get("from"),
                    subject1,
                    plaintextBody,
                    htmlBody
                );

                log.infof("Admin approval email sent for user: %s, email: %s to admin email: %s",
                        user.getUsername(), user.getEmail(), realm.getSmtpConfig().get("from"));
            }catch (EmailException e) {
                log.errorf("Failed to send email for user: %s, email: %s", user.getUsername(), user.getEmail(), e);
            } catch (FreeMarkerException e) {
                log.errorf("Failed to process email template for user: %s, email: %s", user.getUsername(), user.getEmail(), e);
            } catch (IOException ex) {
                log.errorf("Failed to read email template for user: %s, email: %s", user.getUsername(), user.getEmail(), ex);
            } catch (Exception e) {
                log.errorf("Unexpected error while sending email for user: %s, email: %s", user.getUsername(), user.getEmail(), e);
            }
        });
    }

    @Override
    public void onEvent(AdminEvent event, boolean includeRepresentation) {
        log.debugf("Received admin event: %s, type: %s, realm: %s, resourcePath: %s, details: %s, representation: %s",
                event.getId(), event.getOperationType(), event.getRealmId(), event.getResourcePath(), event.getDetails(), event.getRepresentation());

        if (event.getResourceType() ==  ResourceType.GROUP_MEMBERSHIP && 
           (event.getOperationType() == OperationType.CREATE || event.getOperationType() == OperationType.DELETE)) {
            if (event.getRealmId() != null ) {
                tx.addAdminEvent(event, includeRepresentation);
            }
        }

    }

    private void sendEmail(AdminEvent event, boolean includeRepresentation) {
        HttpRequest request = session.getContext().getHttpRequest();

        runJobInTransaction(sessionFactory, new KeycloakSessionTask() {
            @Override
            public void run(KeycloakSession session) {
                KeycloakContext context = session.getContext();
                RealmModel realm = session.realms().getRealm(event.getRealmId());

                context.setRealm(realm);
                context.setHttpRequest(request);

                Map<String, String> details = event.getDetails();
                String username = details.get("username"); 

                if (username == null) {
                    log.warnf("Username not found in event details: %s. Unable to check scout-user group membership.", event.getDetails());
                    return;
                }
                
                // User account approved, send approval email
                if (event.getOperationType() == OperationType.CREATE) {

                    // Representation is a string instead of a nice map, so we check if it contains the scout-user group string
                    boolean isScoutUserAdded = (event.getRepresentation() != null && event.getRepresentation().contains("scout-user"));
                    
                    if (isScoutUserAdded) {
                        try {
                            EmailTemplateProvider emailTemplateProvider = session.getProvider(EmailTemplateProvider.class);
                            emailTemplateProvider.setRealm(realm);
                            emailTemplateProvider.setUser(session.users().getUserByUsername(realm, username));

                            String subjectFormatKey = "Welcome to Scout";
                            String bodyTemplate = "email-user-enabled.ftl";
                            Map<String, Object> bodyAttributes = new java.util.HashMap<>();

                            emailTemplateProvider.send(subjectFormatKey, bodyTemplate, bodyAttributes);

                            log.info("Approval email sent to user: " + username);
                        } catch (EmailException e) {
                            log.error("Failed to send user approval email for user: " + username, e);
                        }
                    }
                }

                // User account disabled, send disabled email
                if (event.getOperationType() == OperationType.DELETE) {
                    
                    // Representation is a string instead of a nice map, so we check if it contains the scout-user group string
                    boolean isScoutUserRemoved = (event.getRepresentation() != null && event.getRepresentation().contains("scout-user"));
                    
                    if (isScoutUserRemoved) {
                        try {
                            EmailTemplateProvider emailTemplateProvider = session.getProvider(EmailTemplateProvider.class);
                            emailTemplateProvider.setRealm(realm);
                            emailTemplateProvider.setUser(session.users().getUserByUsername(realm, username));

                            String subjectFormatKey = "Scout Account Disabled";
                            String bodyTemplate = "email-user-disabled.ftl";
                            Map<String, Object> bodyAttributes = new java.util.HashMap<>();

                            emailTemplateProvider.send(subjectFormatKey, bodyTemplate, bodyAttributes);

                            log.info("Disabled email sent to user: " + username);
                        } catch (EmailException e) {
                            log.error("Failed to send user disabled email for user: " + username, e);
                        }
                    }
                }
            }
        });
    }

    @Override
    public void close() {
    }

}
