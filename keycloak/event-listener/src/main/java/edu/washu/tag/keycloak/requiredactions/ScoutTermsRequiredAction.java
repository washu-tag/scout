package edu.washu.tag.keycloak.requiredactions;

import jakarta.ws.rs.core.Response;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Locale;
import java.util.Map;
import java.util.Properties;
import org.jboss.logging.Logger;
import org.keycloak.Config;
import org.keycloak.authentication.InitiatedActionSupport;
import org.keycloak.authentication.RequiredActionContext;
import org.keycloak.authentication.RequiredActionFactory;
import org.keycloak.authentication.RequiredActionProvider;
import org.keycloak.common.util.Time;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserModel;
import org.keycloak.theme.Theme;

/**
 * Required action that gates every login on acceptance of the current Scout
 * Terms of Use. The "version" of the terms is a SHA-256 hash of the
 * {@code termsBody} message the user is about to see — resolved at request
 * time in the realm's default locale, with realm-level localization overrides
 * taking precedence over the scout login theme's baked-in default.
 *
 * <p>Any edit to {@code termsBody} (via the admin realm-localization API or
 * by rebuilding the theme's {@code messages_en.properties}) produces a new
 * hash; users whose stored acceptance hash no longer matches are re-prompted
 * on their next login. No external version marker to stamp — the SPI derives
 * the version from the same content it is about to render.
 *
 * <p>Per-user state:
 * <ul>
 *   <li>{@code scout_terms_hash_accepted} — the hash value the user last
 *       clicked Continue on.</li>
 *   <li>{@code scout_terms_accepted_at} — unix timestamp of that click.</li>
 * </ul>
 *
 * <p>Feature toggle: if {@code termsBody} resolves to empty (missing from
 * both the realm overrides and the theme properties), the action is a no-op
 * and nothing is added to anyone's requiredActions.
 */
public class ScoutTermsRequiredAction implements RequiredActionProvider, RequiredActionFactory {

    public static final String ID = "SCOUT_TERMS";
    public static final String USER_HASH_ATTR = "scout_terms_hash_accepted";
    public static final String USER_ACCEPTED_AT_ATTR = "scout_terms_accepted_at";
    private static final String FORM_TEMPLATE = "terms.ftl";
    private static final String TERMS_BODY_KEY = "termsBody";

    private static final Logger log = Logger.getLogger(ScoutTermsRequiredAction.class);

    /**
     * Called during the authentication flow to decide whether to require the
     * action for the current user. We add the action when the current
     * termsBody hash differs from the hash the user last accepted.
     *
     * @param context the authentication flow context
     */
    @Override
    public void evaluateTriggers(RequiredActionContext context) {
        String currentHash = computeCurrentHash(context);
        if (currentHash == null) {
            return;
        }

        UserModel user = context.getUser();
        String userHash = user.getFirstAttribute(USER_HASH_ATTR);
        if (currentHash.equals(userHash)) {
            return;
        }

        log.debugf(
                "Adding %s to required actions for user %s (current=%s accepted=%s)",
                ID, user.getUsername(), currentHash, userHash);
        user.addRequiredAction(ID);
    }

    /**
     * Render the Scout Terms of Use page. Uses the existing {@code terms.ftl}
     * template in the scout login theme, which reads message keys
     * {@code termsTitle}, {@code termsBody}, {@code doAccept}, and
     * {@code doDecline}.
     *
     * @param context the authentication flow context
     */
    @Override
    public void requiredActionChallenge(RequiredActionContext context) {
        Response challenge = context.form().createForm(FORM_TEMPLATE);
        context.challenge(challenge);
    }

    /**
     * Process the form submission. Cancel fails the flow (user signed out);
     * accept stamps the user's attributes with the current termsBody hash
     * and the acceptance timestamp.
     *
     * @param context the authentication flow context
     */
    @Override
    public void processAction(RequiredActionContext context) {
        if (context.getHttpRequest().getDecodedFormParameters().containsKey("cancel")) {
            context.failure();
            return;
        }

        String currentHash = computeCurrentHash(context);
        UserModel user = context.getUser();

        user.setSingleAttribute(USER_HASH_ATTR, currentHash == null ? "" : currentHash);
        user.setSingleAttribute(USER_ACCEPTED_AT_ATTR, String.valueOf(Time.currentTime()));
        log.infof(
                "User %s accepted Scout Terms of Use (hash=%s)",
                user.getUsername(), currentHash);
        context.success();
    }

    /**
     * SHA-256 (truncated to 16 hex chars) of the current termsBody message,
     * resolved in the realm's default locale with realm-level localization
     * overrides taking precedence over the theme's baked-in default. Returns
     * {@code null} when no termsBody is defined, which disables the feature.
     */
    private String computeCurrentHash(RequiredActionContext context) {
        RealmModel realm = context.getRealm();
        String locale = realm.getDefaultLocale();
        if (locale == null) {
            return null;
        }

        Map<String, String> overrides = realm.getRealmLocalizationTextsByLocale(locale);
        String body = overrides != null ? overrides.get(TERMS_BODY_KEY) : null;

        if (body == null) {
            try {
                Theme theme = context.getSession().theme().getTheme(Theme.Type.LOGIN);
                Properties themeMessages = theme.getMessages(Locale.forLanguageTag(locale));
                body = themeMessages.getProperty(TERMS_BODY_KEY);
            } catch (IOException e) {
                log.warnf(e, "Unable to resolve login theme messages for %s", ID);
                return null;
            }
        }

        if (body == null || body.isBlank()) {
            return null;
        }

        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(body.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest).substring(0, 16);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 not available", e);
        }
    }

    @Override
    public RequiredActionProvider create(KeycloakSession session) {
        return this;
    }

    @Override
    public void init(Config.Scope config) {
        // no-op
    }

    @Override
    public void postInit(KeycloakSessionFactory factory) {
        // no-op
    }

    @Override
    public void close() {
        // no-op
    }

    @Override
    public String getId() {
        return ID;
    }

    @Override
    public String getDisplayText() {
        return "Accept Scout Terms of Use";
    }

    @Override
    public boolean isOneTimeAction() {
        // false: evaluateTriggers is re-consulted on every login so hash
        // changes trigger a fresh prompt for already-accepted users.
        return false;
    }

    @Override
    public InitiatedActionSupport initiatedActionSupport() {
        return InitiatedActionSupport.NOT_SUPPORTED;
    }
}
