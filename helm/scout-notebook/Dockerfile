FROM quay.io/jupyter/scipy-notebook:python-3.13.13

# Install Scout platform packages and Jupyter extensions via mamba.
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
