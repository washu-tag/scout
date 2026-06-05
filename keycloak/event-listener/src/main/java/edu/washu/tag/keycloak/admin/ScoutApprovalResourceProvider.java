package edu.washu.tag.keycloak.admin;

import org.keycloak.models.KeycloakSession;
import org.keycloak.services.resource.RealmResourceProvider;

/** Per-request provider that hands Keycloak the JAX-RS approval resource. */
public class ScoutApprovalResourceProvider implements RealmResourceProvider {

    private final KeycloakSession session;

    ScoutApprovalResourceProvider(KeycloakSession session) {
        this.session = session;
    }

    /** {@return the JAX-RS approval resource bound to this request's session} */
    @Override
    public Object getResource() {
        return new ScoutApprovalResource(session);
    }

    @Override
    public void close() {
    }
}
