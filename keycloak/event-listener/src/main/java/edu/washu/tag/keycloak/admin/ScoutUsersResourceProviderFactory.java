package edu.washu.tag.keycloak.admin;

import org.keycloak.Config;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.services.resource.RealmResourceProvider;
import org.keycloak.services.resource.RealmResourceProviderFactory;

/**
 * Registers Scout's user-approval REST resource under
 * {@code /realms/{realm}/scout-users}. The resource lets a scout-admin list
 * pending users and approve them (join scout-user + set data-access attributes)
 * in one call, acting as the calling admin — no standing admin credential.
 *
 * <p>The set of data-access attributes is discovered dynamically from the realm
 * User Profile (attributes annotated {@code scoutAuthz=true}), so adding a new
 * dimension to {@code trino_attribute_filters} surfaces in the approval flow
 * with no change here. See {@link ScoutUsersResource}.
 */
public class ScoutUsersResourceProviderFactory implements RealmResourceProviderFactory {

    public static final String ID = "scout-users";

    @Override
    public RealmResourceProvider create(KeycloakSession session) {
        return new ScoutUsersResourceProvider(session);
    }

    @Override
    public void init(Config.Scope config) {
    }

    @Override
    public void postInit(KeycloakSessionFactory factory) {
    }

    @Override
    public void close() {
    }

    @Override
    public String getId() {
        return ID;
    }
}
