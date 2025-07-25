FROM quay.io/keycloak/keycloak:latest AS builder

# Keycloak build needs some of these environment variables set
ENV KC_HEALTH_ENABLED=true
ENV KC_METRICS_ENABLED=true
ENV KC_EVENT_METRICS_USER_ENABLED=true
ENV KC_EVENT_METRICS_USER_TAGS=realm,idp,clientId
ENV KC_DB=postgres
ENV KC_HTTP_RELATIVE_PATH=/kc

WORKDIR /opt/keycloak

COPY event-listener/build/libs/*.jar /opt/keycloak/providers/

RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:latest
COPY --from=builder /opt/keycloak/ /opt/keycloak/
