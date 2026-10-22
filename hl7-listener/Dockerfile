# Build the Camel-on-Spring-Boot HL7 listener application (executable bootJar).
FROM amazoncorretto:21-alpine AS build

WORKDIR /app

# Copy the Gradle wrapper and prime it
COPY gradlew .
COPY gradle gradle
RUN chmod +x gradlew
RUN ./gradlew --version

# Copy the source code
COPY build.gradle .
COPY settings.gradle .
COPY src src

# Build application, skipping tests
RUN ./gradlew build -x test

# Use a smaller base image for the final stage
FROM amazoncorretto:21-alpine

# libssl3/libcrypto3 repeatedly lag Alpine's OpenSSL patches on this base image;
# pull latest explicitly instead of waiting on a rebuild. No-op once caught up.
RUN apk add --no-cache --upgrade libssl3 libcrypto3 ca-certificates

# Drop the trust anchors the compromised eMudhra intermediates chain to (CVE-2026-91403)
# until the JDK's cacerts is fixed upstream. Fails the build if either survives.
RUN ks="$(java -XshowSettings:properties -version 2>&1 | sed -n 's/^ *java.home = //p')/lib/security/cacerts" \
    && bad() { out="$(keytool -list -v -keystore "$ks" -storepass changeit 2>/dev/null)" \
         && case "$out" in *"Alias name: "*) ;; *) echo "cannot list $ks" >&2; return 1 ;; esac \
         && printf '%s\n' "$out" | while IFS= read -r l; do \
              case "$l" in "Alias name: "*) a=${l#Alias name: } ;; \
                "Owner: "*"emSign ECC Root CA - G3"*|"Owner: "*"AAA Certificate Services"*) printf '%s\n' "$a" ;; esac; \
            done; } \
    && aliases="$(bad)" \
    && printf '%s\n' "$aliases" | while IFS= read -r a; do [ -z "$a" ] || keytool -delete -alias "$a" -keystore "$ks" -storepass changeit; done \
    && left="$(bad)" && [ -z "$left" ]

WORKDIR /app

# Copy the executable bootJar from the build stage
COPY --from=build /app/build/libs/*.jar app.jar

# Run as a non-root user. Defense-in-depth: the Helm chart also sets
# runAsNonRoot/runAsUser: 1000, but pinning it here keeps the image non-root if it's
# ever run outside that chart, and satisfies image-scanning (no root ENTRYPOINT).
USER 1000

ENTRYPOINT ["java", "-jar", "app.jar"]
