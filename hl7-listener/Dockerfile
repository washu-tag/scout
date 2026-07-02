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

WORKDIR /app

# Copy the executable bootJar from the build stage
COPY --from=build /app/build/libs/*.jar app.jar

ENTRYPOINT ["java", "-jar", "app.jar"]
