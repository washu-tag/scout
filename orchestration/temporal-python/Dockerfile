# Stage 1: Cache pip packages into wheels
FROM python:3.12-slim AS builder

# Set the working directory for the build
WORKDIR /build

# Copy the current directory contents into the container at /build
COPY . /build

# Install wheel to build wheels
RUN pip install wheel

# Build wheels for the dependencies
RUN pip wheel . -w /wheels

# Stage 2: Build the main app and run the Temporal worker
FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

# Copy the built wheels from the builder stage
COPY --from=builder /wheels /wheels

# Install the wheels
RUN pip install --no-cache-dir /wheels/*

# Copy the current directory contents into the container at /app
COPY . /app

# Define environment variable
ENV PYTHONUNBUFFERED=1

# Run the worker
ENTRYPOINT ["python", "-m", "temporalpy.ingesthl7worker"]
