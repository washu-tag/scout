# Use an official OpenJDK 21 runtime as a parent image
FROM amazoncorretto:21-alpine AS build

# Set the working directory in the container
WORKDIR /app

# Copy the Gradle wrapper and build files
COPY gradlew .
COPY gradle gradle
COPY build.gradle .
COPY settings.gradle .

# Copy the source code
COPY src src

# Make the Gradle wrapper executable and build the application
RUN chmod +x gradlew
RUN ./gradlew build

# Use a smaller base image for the final stage
FROM amazoncorretto:21-alpine

# Set the working directory in the container
WORKDIR /app

# This is necessary for some of the scripts to work
RUN apk add coreutils

# Copy the built JAR file from the build stage
COPY --from=build /app/build/libs/*.jar app.jar

# Copy scripts
COPY scripts /app/scripts

# Run the jar file
ENTRYPOINT ["java", "-jar", "app.jar"]
