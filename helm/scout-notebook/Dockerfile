FROM quay.io/jupyter/scipy-notebook:python-3.13.13

# Platform packages + Jupyter extensions via mamba. Everything else
# (torch, transformers, scikit-learn, etc.) is user-installed via the
# package proxy.
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

# Scout SDK (scout.trino, scout.voila). Installed system-wide so it's
# importable from every kernel without per-user setup.
COPY --chown=root:root scout-sdk /tmp/scout-sdk
RUN pip install --no-cache-dir /tmp/scout-sdk[voila] && rm -rf /tmp/scout-sdk
