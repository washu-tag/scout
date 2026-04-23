package edu.washu.tag.keycloak.requiredactions;

import jakarta.ws.rs.core.Response;
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

/**
 * Required action that gates every login on acceptance of the current Scout
 * Terms of Use. Uses a content hash (stored as realm attribute
 * `scout_terms_hash`) as the version identifier: any change to the terms
 * text produces a new hash, all users whose stored acceptance hash differs
 * get the action on their next login.
 *
 * <p>Why a hash and not an integer version: the hash is derived from the
 * terms content itself (computed by Ansible from
 * `keycloak_terms_localizations`) so the realm attribute and the text
 * shipped to users can never diverge. No separate "version" variable for
 * the operator to forget to bump.
 *
 * <p>Per-user state:
 * <ul>
 *   <li>{@code scout_terms_hash_accepted} — the hash value the user last
 *       clicked Continue on.</li>
 *   <li>{@code scout_terms_accepted_at} — unix timestamp of that click.</li>
 * </ul>
 *
 * <p>Feature toggle: if the realm has no {@code scout_terms_hash} attribute,
 * the action is a no-op — nothing is added to anyone's requiredActions.
 * Removing the attribute from the realm cleanly disables the feature.
 */
public class ScoutTermsRequiredAction implements RequiredActionProvider, RequiredActionFactory {

    public static final String ID = "SCOUT_TERMS";
    public static final String REALM_HASH_ATTR = "scout_terms_hash";
    public static final String USER_HASH_ATTR = "scout_terms_hash_accepted";
    public static final String USER_ACCEPTED_AT_ATTR = "scout_terms_accepted_at";
    private static final String FORM_TEMPLATE = "terms.ftl";

    private static final Logger log = Logger.getLogger(ScoutTermsRequiredAction.class);

    /**
     * Called during the authentication flow to decide whether to require the
     * action for the current user. We add the action when the realm's current
     * terms hash differs from the hash the user last accepted.
     *
     * @param context the authentication flow context
     */
    @Override
    public void evaluateTriggers(RequiredActionContext context) {
        RealmModel realm = context.getRealm();
        String realmHash = realm.getAttribute(REALM_HASH_ATTR);
        if (realmHash == null || realmHash.isBlank()) {
            return;
        }

        UserModel user = context.getUser();
        String userHash = user.getFirstAttribute(USER_HASH_ATTR);
        if (realmHash.equals(userHash)) {
            return;
        }

        log.debugf(
                "Adding %s to required actions for user %s (realm hash=%s user hash=%s)",
                ID, user.getUsername(), realmHash, userHash);
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
     * accept stamps the user's attributes with the current realm hash and
     * the acceptance timestamp.
     *
     * @param context the authentication flow context
     */
    @Override
    public void processAction(RequiredActionContext context) {
        if (context.getHttpRequest().getDecodedFormParameters().containsKey("cancel")) {
            context.failure();
            return;
        }

        RealmModel realm = context.getRealm();
        String realmHash = realm.getAttribute(REALM_HASH_ATTR);
        UserModel user = context.getUser();

        user.setSingleAttribute(USER_HASH_ATTR, realmHash == null ? "" : realmHash);
        user.setSingleAttribute(USER_ACCEPTED_AT_ATTR, String.valueOf(Time.currentTime()));
        log.infof(
                "User %s accepted Scout Terms of Use (hash=%s)",
                user.getUsername(), realmHash);
        context.success();
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
