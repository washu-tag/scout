package edu.washu.tag.keycloak.events;

import java.util.List;
import org.keycloak.Config;
import org.keycloak.events.EventListenerProviderFactory;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.provider.ProviderConfigProperty;
import org.keycloak.provider.ProviderConfigurationBuilder;

/**
 * Factory for creating UserApprovalEmailEventListenerProvider instances.
 */
public class UserApprovalEmailEventListenerProviderFactory implements EventListenerProviderFactory {

    public static final String ID = "user-approval-email";
    
    @Override
    public UserApprovalEmailEventListenerProvider create(KeycloakSession session) {
        return new UserApprovalEmailEventListenerProvider(session);
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

    @Override
    public List<ProviderConfigProperty> getConfigMetadata() {
        return ProviderConfigurationBuilder.create().build();
    }

}