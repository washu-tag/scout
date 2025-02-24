FROM quay.io/jupyter/pyspark-notebook:spark-3.5.3

COPY --chown=${NB_UID}:${NB_GID} requirements.txt /tmp/

RUN python3 -m pip install --no-cache-dir -r /tmp/requirements.txt && \
    fix-permissions "${CONDA_DIR}" && \
    fix-permissions "/home/${NB_USER}" && \
    rm /tmp/requirements.txt