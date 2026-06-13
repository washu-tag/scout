FROM spark:4.1.1-scala2.13-java21-python3-ubuntu

USER root

RUN set -ex; \
    apt-get update; \
    apt-get install -y python3.10-venv; \
    rm -rf /var/lib/apt/lists/*

USER spark

# Set the working directory
WORKDIR /app

# Delta Lake and S3A jars. hadoop-aws must match the hadoop-client version
# bundled in the base image, and the awssdk bundle must match the
# aws-java-sdk-v2 version that hadoop-aws declares in hadoop-project's pom.
ADD --chown=spark \
    https://repo1.maven.org/maven2/io/delta/delta-spark_4.1_2.13/4.2.0/delta-spark_4.1_2.13-4.2.0.jar \
    https://repo1.maven.org/maven2/io/delta/delta-storage/4.2.0/delta-storage-4.2.0.jar \
    https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.4.2/hadoop-aws-3.4.2.jar \
    https://repo1.maven.org/maven2/software/amazon/awssdk/bundle/2.29.52/bundle-2.29.52.jar \
    ${SPARK_HOME}/jars/

# Create venv
RUN python3 -m venv venv

# Copy the pyproject.toml so we can install the dependencies first
COPY --chown=spark pyproject.toml /app
RUN venv/bin/python3 -m pip install --no-cache .

# Copy the current directory contents into the container at /app
COPY --chown=spark . /app

# Install the applicationp
RUN venv/bin/python3 -m pip install .

# Define environment variable
ENV PYTHONUNBUFFERED=1

# Run the worker
CMD ["venv/bin/python3", "-m", "hl7scout.ingesthl7worker"]
