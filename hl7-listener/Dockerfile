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
RUN apk add --no-cache --upgrade libssl3 libcrypto3

WORKDIR /app

# Copy the executable bootJar from the build stage
COPY --from=build /app/build/libs/*.jar app.jar

# Run as a non-root user. Defense-in-depth: the Helm chart also sets
# runAsNonRoot/runAsUser: 1000, but pinning it here keeps the image non-root if it's
# ever run outside that chart, and satisfies image-scanning (no root ENTRYPOINT).
USER 1000

ENTRYPOINT ["java", "-jar", "app.jar"]
