FROM quay.io/jupyter/pyspark-notebook:spark-3.5.3

# Copy spark jars, be sure to download the same jars in the pyspark notebook image and the temporal python worker image
ADD --chown=${NB_UID}:${NB_GID} \
    https://repo1.maven.org/maven2/io/delta/delta-spark_2.12/3.3.0/delta-spark_2.12-3.3.0.jar \
    https://repo1.maven.org/maven2/io/delta/delta-storage/3.3.0/delta-storage-3.3.0.jar \
    https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.2.2/hadoop-aws-3.2.2.jar \
    https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-common/3.2.2/hadoop-common-3.2.2.jar \
    https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-hdfs/3.2.2/hadoop-hdfs-3.2.2.jar \
    https://repo1.maven.org/maven2/org/antlr/antlr4-runtime/4.9.3/antlr4-runtime-4.9.3.jar \
    https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.12.793/aws-java-sdk-bundle-1.12.793.jar \
    https://github.com/washu-tag/smolder/releases/download/0.1.0-20250306/smolder_2.12-0.1.0-SNAPSHOT.jar \
    ${SPARK_HOME}/jars/

# Install Scout platform packages and Jupyter extensions via mamba.
# Everything else (torch, transformers, scikit-learn, etc.) is user-installed
# via conda environments through the package proxy.
RUN mamba install -y -n base \
    trino-python-client \
    s3fs \
    boto3 \
    voila \
    jupyter-server-proxy \
    jupyterlab-nvdashboard \
    jupyter-resource-usage \
    nb_conda_kernels \
    && mamba clean -afy \
    && pip install --no-deps --no-cache-dir delta-spark==3.3.0 \
    && fix-permissions "${CONDA_DIR}" \
    && fix-permissions "/home/${NB_USER}"
