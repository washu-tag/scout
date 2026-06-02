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
# importable from every kernel without per-user setup. Lives at repo
# root (sdk/python/) so build context must be the repo root, not this
# directory — see docker-build-cache action's `context` input.
#
# --chown to NB_UID:NB_GID because setuptools' PEP 517 build_meta
# writes an egg-info dir into the source tree during the
# get_requires_for_build_wheel hook; the base image runs as jovyan, so
# a root-owned source dir fails the install with "Permission denied".
COPY --chown=${NB_UID}:${NB_GID} sdk/python /tmp/scout-sdk
RUN pip install --no-cache-dir /tmp/scout-sdk[voila] && rm -rf /tmp/scout-sdk
