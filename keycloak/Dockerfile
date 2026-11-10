FROM gradle:9.8.0-jdk21 AS extension-builder

COPY --chown=gradle:gradle event-listener/ /app/event-listener/
WORKDIR /app/event-listener
RUN gradle --no-daemon --stacktrace jar


FROM quay.io/keycloak/keycloak:26.8.1 AS kc-builder

# Keycloak build requires some environment variables to be set
ENV KC_HEALTH_ENABLED=true
ENV KC_METRICS_ENABLED=true
ENV KC_EVENT_METRICS_USER_ENABLED=true
ENV KC_EVENT_METRICS_USER_TAGS=realm,idp,clientId
ENV KC_DB=postgres

# Build Keycloak with extensions
WORKDIR /opt/keycloak
COPY --from=extension-builder /app/event-listener/build/libs/*.jar /opt/keycloak/providers/
RUN /opt/keycloak/bin/kc.sh build


FROM quay.io/keycloak/keycloak:26.8.1
COPY --from=kc-builder /opt/keycloak/ /opt/keycloak/

USER root
# Drop the trust anchors the compromised intermediates chain to (CVE-2026-91403). cacerts
# links to UBI's /etc/pki/ca-trust/extracted/java/cacerts, so this can go once UBI 9's
# ca-certificates drops them. Fails the build if either survives.
RUN ks="$(java -XshowSettings:properties -version 2>&1 | sed -n 's/^ *java.home = //p')/lib/security/cacerts" \
    && bad() { out="$(keytool -list -v -keystore "$ks" -storepass changeit 2>&1)" || { printf '%s\n' "$out" >&2; return 1; }; \
         case "$out" in *"Alias name: "*"Owner: CN="*) ;; *) printf 'unexpected keytool output for %s\n%s\n' "$ks" "$out" >&2; return 1 ;; esac; \
         printf '%s\n' "$out" | while IFS= read -r l; do \
           case "$l" in "Alias name: "*) a=${l#Alias name: } ;; \
             "Owner: "*"emSign ECC Root CA - G3"*|"Owner: "*"AAA Certificate Services"*) printf '%s\n' "$a" ;; esac; \
         done; } \
    && aliases="$(bad)" \
    && printf '%s\n' "$aliases" | while IFS= read -r a; do [ -z "$a" ] || keytool -delete -alias "$a" -keystore "$ks" -storepass changeit; done \
    && left="$(bad)" \
    && { [ -z "$left" ] || { printf 'anchors still in %s: %s\n' "$ks" "$left" >&2; exit 1; }; }
USER 1000
