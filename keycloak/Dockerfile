FROM gradle:8.14.3-jdk21 as extension-builder

COPY --chown=gradle:gradle event-listener/ /app/event-listener/
WORKDIR /app/event-listener
RUN gradle --no-daemon --stacktrace jar


FROM quay.io/keycloak/keycloak:latest AS kc-builder

# Keycloak build requires some environment variables to be set
ENV KC_HEALTH_ENABLED=true
ENV KC_METRICS_ENABLED=true
ENV KC_EVENT_METRICS_USER_ENABLED=true
ENV KC_EVENT_METRICS_USER_TAGS=realm,idp,clientId
ENV KC_DB=postgres
ENV KC_HTTP_RELATIVE_PATH=/kc

# Build Keycloak with extensions
WORKDIR /opt/keycloak
COPY --from=extension-builder /app/event-listener/build/libs/*.jar /opt/keycloak/providers/
RUN /opt/keycloak/bin/kc.sh build


FROM quay.io/keycloak/keycloak:26.3.2
COPY --from=kc-builder /opt/keycloak/ /opt/keycloak/
