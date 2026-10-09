FROM quay.io/jupyter/scipy-notebook:python-3.13.15

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade openssl libssl3t64 \
    && rm -rf /var/lib/apt/lists/*
USER ${NB_UID}

# Platform packages + Jupyter extensions via mamba. Everything else
# (torch, transformers, scikit-learn, etc.) is user-installed via the
# package proxy. Security floors override vulnerable base packages; drop them
# once a newer scipy-notebook base includes these fixes.
RUN mamba install -y -n base \
    trino-python-client \
    voila \
    jupyter-server-proxy \
    jupyterlab-nvdashboard \
    jupyter-resource-usage \
    nb_conda_kernels \
    'gitpython>=3.1.59' \
    'jupyterlab>=4.6.4' \
    'notebook>=7.6.3' \
    'urllib3>=2.8.0' \
    'tornado>=6.5.9' \
    'jupyter_server>=2.21.0' \
    'pyjwt>=2.14.0' \
    && mamba clean -afy \
    && fix-permissions "${CONDA_DIR}" \
    && fix-permissions "/home/${NB_USER}"

# Scout SDK (scout.query, scout.connect, scout.current_user). Installed
# system-wide so it's importable from every kernel without per-user setup.
# Lives at repo root (sdk/python/) so build context must be the repo root,
# not this directory — see docker-build-cache action's `context` input.
#
# --chown to NB_UID:NB_GID because setuptools' PEP 517 build_meta writes
# an egg-info dir into the source tree during the
# get_requires_for_build_wheel hook; the base image runs as jovyan, so a
# root-owned source dir fails the install with "Permission denied".
COPY --chown=${NB_UID}:${NB_GID} sdk/python /tmp/scout-sdk
RUN pip install --no-cache-dir /tmp/scout-sdk && rm -rf /tmp/scout-sdk
