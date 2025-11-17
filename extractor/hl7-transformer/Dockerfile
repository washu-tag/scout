FROM spark:3.5.4-scala2.12-java17-python3-ubuntu

USER root

RUN set -ex; \
    apt-get update; \
    apt-get install -y python3.10-venv; \
    rm -rf /var/lib/apt/lists/*

USER spark

# Set the working directory
WORKDIR /app

# Copy spark jars, be sure to download the same jars in the pyspark notebook image and the temporal python worker image
ADD --chown=spark \
    https://repo1.maven.org/maven2/io/delta/delta-spark_2.12/3.3.0/delta-spark_2.12-3.3.0.jar \
    https://repo1.maven.org/maven2/io/delta/delta-storage/3.3.0/delta-storage-3.3.0.jar \
    https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.2.2/hadoop-aws-3.2.2.jar \
    https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-common/3.2.2/hadoop-common-3.2.2.jar \
    https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-hdfs/3.2.2/hadoop-hdfs-3.2.2.jar \
    https://repo1.maven.org/maven2/org/antlr/antlr4-runtime/4.9.3/antlr4-runtime-4.9.3.jar \
    https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.12.772/aws-java-sdk-bundle-1.12.772.jar \
    https://github.com/washu-tag/smolder/releases/download/0.1.0-20250306/smolder_2.12-0.1.0-SNAPSHOT.jar \
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
