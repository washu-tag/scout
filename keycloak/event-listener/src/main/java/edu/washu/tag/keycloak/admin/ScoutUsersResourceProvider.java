package edu.washu.tag.keycloak.admin;

import org.keycloak.models.KeycloakSession;
import org.keycloak.services.resource.RealmResourceProvider;

/** Per-request provider that hands Keycloak the JAX-RS approval resource. */
public class ScoutUsersResourceProvider implements RealmResourceProvider {

    private final KeycloakSession session;

    ScoutUsersResourceProvider(KeycloakSession session) {
        this.session = session;
    }

    /** {@return the JAX-RS approval resource bound to this request's session} */
    @Override
    public Object getResource() {
        return new ScoutUsersResource(session);
    }

    @Override
    public void close() {
    }
}
