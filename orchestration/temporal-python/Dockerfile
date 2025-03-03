FROM spark:3.5.4-scala2.12-java17-python3-ubuntu

USER root

RUN set -ex; \
    apt-get update; \
    apt-get install -y python3.10-venv; \
    rm -rf /var/lib/apt/lists/*

USER spark

# Set the working directory
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY --chown=spark . /app

# Install the applicationp
RUN python3 -m venv venv; venv/bin/pip install .

# Define environment variable
ENV PYTHONUNBUFFERED=1

# Run the worker
ENTRYPOINT ["venv/bin/python3", "-m", "temporalpy.ingesthl7worker"]
