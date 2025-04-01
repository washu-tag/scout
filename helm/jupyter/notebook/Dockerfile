FROM quay.io/jupyter/pyspark-notebook:spark-3.5.3

COPY --chown=${NB_UID}:${NB_GID} requirements.txt /tmp/

# Copy spark jars
ADD --chown=${NB_UID}:${NB_GID} \
    https://repo1.maven.org/maven2/io/delta/delta-spark_2.12/3.3.0/delta-spark_2.12-3.3.0.jar \
    https://repo1.maven.org/maven2/io/delta/delta-storage/3.3.0/delta-storage-3.3.0.jar \
    https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.2.2/hadoop-aws-3.2.2.jar \
    https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-common/3.2.2/hadoop-common-3.2.2.jar \
    https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-hdfs/3.2.2/hadoop-hdfs-3.2.2.jar \
    https://repo1.maven.org/maven2/org/antlr/antlr4-runtime/4.9.3/antlr4-runtime-4.9.3.jar \
    https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.11.563/aws-java-sdk-bundle-1.11.563.jar \
    https://github.com/washu-tag/smolder/releases/download/0.1.0-20250306/smolder_2.12-0.1.0-SNAPSHOT.jar \
    /opt/spark/jars/

RUN python3 -m pip install --no-cache-dir -r /tmp/requirements.txt && \
    fix-permissions "${CONDA_DIR}" && \
    fix-permissions "/home/${NB_USER}" && \
    rm /tmp/requirements.txt