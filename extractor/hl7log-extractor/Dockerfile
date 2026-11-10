# Use an official OpenJDK 21 runtime as a parent image
FROM amazoncorretto:21-alpine AS build

# Set the working directory in the container
WORKDIR /app

# Copy the Gradle wrapper and build files
COPY gradlew .
COPY gradle gradle

# Make the Gradle wrapper executable and download gradle
RUN chmod +x gradlew
RUN ./gradlew --version

# Copy the source code
COPY build.gradle .
COPY settings.gradle .
COPY src src

# Build application, ignoring test and linting jobs
RUN ./gradlew build -x test -x checkstyleMain -x checkstyleTest

# Use a smaller base image for the final stage
FROM amazoncorretto:21-alpine

# libssl3/libcrypto3 repeatedly lag Alpine's OpenSSL patches on this base image;
# pull latest explicitly instead of waiting on a rebuild. No-op once caught up.
RUN apk add --no-cache --upgrade libssl3 libcrypto3

# Drop the trust anchors the compromised intermediates chain to (CVE-2026-91403)
# until the JDK's cacerts is fixed upstream. Fails the build if either survives.
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

# Set the working directory in the container
WORKDIR /app

# Copy the built JAR file from the build stage
COPY --from=build /app/build/libs/*.jar app.jar

# Run as an unprivileged user rather than root
RUN addgroup -S app && adduser -S -G app app
USER app

# Run the jar file
ENTRYPOINT ["java", "-jar", "app.jar"]
