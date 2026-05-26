FROM quay.io/jupyter/scipy-notebook:lab-4.2.5

# Install Scout platform packages and Jupyter extensions via mamba.
# Trino is the canonical query interface for notebooks — there is no Spark
# and no direct MinIO/S3 access from notebook pods. All HL7 / Delta Lake
# reads go through Trino (which enforces AuthZ via OPA).
#
# Everything else (torch, transformers, scikit-learn, etc.) is user-installed
# via conda environments through the package proxy.
RUN mamba install -y -n base \
    trino-python-client \
    voila \
    jupyter-server-proxy \
    jupyterlab-nvdashboard \
    jupyter-resource-usage \
    nb_conda_kernels \
    && mamba clean -afy \
    && fix-permissions "${CONDA_DIR}" \
    && fix-permissions "/home/${NB_USER}"
