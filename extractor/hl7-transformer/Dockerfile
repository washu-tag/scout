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
# bundled in the base image. The awssdk bundle must be at least the
# aws-java-sdk-v2 version that hadoop-aws declares in hadoop-project's pom
# (2.29.52 for 3.4.2); newer 2.x releases are compatible and carry SDK and
# embedded-netty CVE fixes.
ADD --chown=spark \
    https://repo1.maven.org/maven2/io/delta/delta-spark_4.1_2.13/4.3.0/delta-spark_4.1_2.13-4.3.0.jar \
    https://repo1.maven.org/maven2/io/delta/delta-storage/4.3.0/delta-storage-4.3.0.jar \
    https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.4.2/hadoop-aws-3.4.2.jar \
    https://repo1.maven.org/maven2/software/amazon/awssdk/bundle/2.49.3/bundle-2.49.3.jar \
    ${SPARK_HOME}/jars/

# Create venv. The seeded setuptools (ubuntu's 59.6.0) has known CVEs.
RUN python3 -m venv venv && venv/bin/pip install --no-cache --upgrade pip setuptools

# Copy the pyproject.toml so we can install the dependencies first
COPY --chown=spark pyproject.toml /app
RUN venv/bin/python3 -m pip install --no-cache .

# Copy the current directory contents into the container at /app
COPY --chown=spark . /app

# Install the application, then trim the runtime image. The pyspark wheel bundles
# a full duplicate of the Spark jars (runtime uses SPARK_HOME's copy), and pip is
# unused at runtime; its vendored msgpack/setuptools are the only fixable CVEs the
# scanner still flags in our layers. Dropping both keeps the image and scan clean.
RUN venv/bin/python3 -m pip install . \
    && rm -rf venv/lib/python3.10/site-packages/pyspark/jars \
              venv/lib/python3.10/site-packages/pip \
              venv/lib/python3.10/site-packages/pip-*.dist-info \
              venv/bin/pip venv/bin/pip3 venv/bin/pip3.10

# Define environment variable
ENV PYTHONUNBUFFERED=1

# Run the worker
CMD ["venv/bin/python3", "-m", "hl7scout.ingesthl7worker"]
