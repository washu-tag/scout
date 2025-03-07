FROM spark:3.5.4-scala2.12-java17-python3-ubuntu

USER root

RUN set -ex; \
    apt-get update; \
    apt-get install -y python3.10-venv; \
    rm -rf /var/lib/apt/lists/*

USER spark

# Set the working directory
WORKDIR /app

# Copy spark jars
# These were found by doing an interactive pyspark session with spark.jars.packages set to
# "org.apache.hadoop:hadoop-aws:3.4.1,io.delta:delta-spark_2.12:3.3.0"
# and inspecting the logs for what got downloaded
ADD --chown=spark \
#    https://repo1.maven.org/maven2/io/delta/delta-spark_2.12/3.3.0/delta-spark_2.12-3.3.0.jar \
#    https://repo1.maven.org/maven2/io/delta/delta-storage/3.3.0/delta-storage-3.3.0.jar \
#    https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.4.1/hadoop-aws-3.4.1.jar \
#    https://repo1.maven.org/maven2/org/antlr/antlr4-runtime/4.9.3/antlr4-runtime-4.9.3.jar \
#    https://repo1.maven.org/maven2/software/amazon/awssdk/bundle/2.24.6/bundle-2.24.6.jar \
    https://github.com/washu-tag/smolder/releases/download/0.1.0-20250306/smolder_2.12-0.1.0-SNAPSHOT.jar \
    /opt/spark/jars/

# Copy the current directory contents into the container at /app
COPY --chown=spark . /app

# Install the applicationp
RUN python3 -m venv venv; venv/bin/pip install .

# Define environment variable
ENV PYTHONUNBUFFERED=1

# Run the worker
ENTRYPOINT ["venv/bin/python3", "-m", "temporalpy.ingesthl7worker"]
