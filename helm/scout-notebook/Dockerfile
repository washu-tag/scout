FROM quay.io/jupyter/scipy-notebook:python-3.13.18

ARG TARGETARCH
# renovate: datasource=github-releases depName=coder/code-server extractVersion=^v(?<version>.+)$
ARG CODE_SERVER_VERSION=4.145.0
# renovate: datasource=npm depName=@anthropic-ai/claude-code
ARG CLAUDE_CODE_VERSION=2.1.312
ARG PYTHON_EXTENSION_VERSION=2026.4.0
ARG JUPYTER_EXTENSION_VERSION=2025.9.1
# renovate: datasource=github-releases depName=raine/workmux extractVersion=^v(?<version>.+)$
ARG WORKMUX_VERSION=0.1.277
# renovate: datasource=github-releases depName=herdrdev/herdr extractVersion=^v(?<version>.+)$
ARG HERDR_VERSION=0.9.5
# renovate: datasource=github-releases depName=openai/codex extractVersion=^rust-v(?<version>.+)$
ARG CODEX_VERSION=0.166.0
# renovate: datasource=github-releases depName=jesseduffield/lazygit extractVersion=^v(?<version>.+)$
ARG LAZYGIT_VERSION=0.66.2

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade openssl libssl3t64 \
        curl libcurl4t64 libcurl3t64-gnutls ca-certificates apt libapt-pkg7.0 libbrotli1 \
    && curl -fsSL --retry 3 --retry-all-errors -o /tmp/code-server.deb \
        "https://github.com/coder/code-server/releases/download/v${CODE_SERVER_VERSION}/code-server_${CODE_SERVER_VERSION}_${TARGETARCH}.deb" \
    && apt-get install -y --no-install-recommends /tmp/code-server.deb ttyd tmux \
        vim ripgrep fd-find jq bubblewrap \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd \
    && rm -rf /tmp/code-server.deb /var/lib/apt/lists/*

# The version checks write state to HOME, which must stay out of /home/jovyan.
RUN export HOME=/tmp/tools-home \
    && cd /tmp \
    && uarch="$(echo "${TARGETARCH}" | sed 's/amd64/x86_64/;s/arm64/aarch64/')" \
    && lgarch="$(echo "${TARGETARCH}" | sed 's/amd64/x86_64/')" \
    && curl -fsSL --retry 3 --retry-all-errors \
        -O "https://github.com/raine/workmux/releases/download/v${WORKMUX_VERSION}/workmux-linux-${TARGETARCH}.tar.gz" \
        -O "https://github.com/raine/workmux/releases/download/v${WORKMUX_VERSION}/workmux-linux-${TARGETARCH}.sha256" \
        -O "https://github.com/jesseduffield/lazygit/releases/download/v${LAZYGIT_VERSION}/lazygit_${LAZYGIT_VERSION}_linux_${lgarch}.tar.gz" \
        -O "https://github.com/jesseduffield/lazygit/releases/download/v${LAZYGIT_VERSION}/checksums.txt" \
        -O "https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/codex-${uarch}-unknown-linux-musl.tar.gz" \
        -O "https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/codex-code-mode-host-${uarch}-unknown-linux-musl.tar.gz" \
        -o /usr/local/bin/herdr "https://github.com/herdrdev/herdr/releases/download/v${HERDR_VERSION}/herdr-linux-${uarch}" \
    && sha256sum -c "workmux-linux-${TARGETARCH}.sha256" \
    && grep " lazygit_${LAZYGIT_VERSION}_linux_${lgarch}.tar.gz\$" checksums.txt | sha256sum -c \
    && tar -xzf "workmux-linux-${TARGETARCH}.tar.gz" -C /usr/local/bin workmux \
    && tar -xzf "lazygit_${LAZYGIT_VERSION}_linux_${lgarch}.tar.gz" -C /usr/local/bin lazygit \
    && tar -xzf "codex-${uarch}-unknown-linux-musl.tar.gz" \
    && tar -xzf "codex-code-mode-host-${uarch}-unknown-linux-musl.tar.gz" \
    && install -m 0755 "codex-${uarch}-unknown-linux-musl" /usr/local/bin/codex \
    && install -m 0755 "codex-code-mode-host-${uarch}-unknown-linux-musl" /usr/local/bin/codex-code-mode-host \
    && chmod 0755 /usr/local/bin/herdr \
    && rm -f /tmp/workmux-linux-* /tmp/lazygit_* /tmp/checksums.txt /tmp/codex-* \
    && codex --version && herdr --version && lazygit --version \
    && rm -rf /tmp/tools-home

# Installed as built-in extensions, since the home volume mounts over the user
# extensions directory. The Claude Code extension bundles the `claude` CLI.
RUN export HOME=/tmp/ext-home \
    && vsix_arch="$(echo "${TARGETARCH}" | sed 's/amd64/x64/')" \
    && curl -fsSL --retry 3 --retry-all-errors -o /tmp/claude-code.vsix \
        "https://open-vsx.org/api/Anthropic/claude-code/linux-${vsix_arch}/${CLAUDE_CODE_VERSION}/file/Anthropic.claude-code-${CLAUDE_CODE_VERSION}@linux-${vsix_arch}.vsix" \
    && code-server --extensions-dir /tmp/ext \
        --install-extension /tmp/claude-code.vsix \
        --install-extension "ms-python.python@${PYTHON_EXTENSION_VERSION}" \
        --install-extension "ms-toolsai.jupyter@${JUPYTER_EXTENSION_VERSION}" \
    && find /tmp/ext -mindepth 1 -maxdepth 1 -type d \
        -exec mv -t /usr/lib/code-server/lib/vscode/extensions/ {} + \
    && ln -s /usr/lib/code-server/lib/vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude \
        /usr/local/bin/claude \
    && claude --version \
    && rm -rf /tmp/ext /tmp/ext-home /tmp/claude-code.vsix

COPY helm/scout-notebook/code-server.yaml /etc/code-server/config.yaml
ENV CODE_SERVER_CONFIG=/etc/code-server/config.yaml
COPY helm/scout-notebook/tmux.conf /etc/tmux.conf
COPY helm/scout-notebook/codex-config.toml /etc/codex/config.toml
COPY helm/scout-notebook/jupyter_server_config.json /usr/local/etc/jupyter/jupyter_server_config.json
USER ${NB_UID}

# Everything else (torch, transformers, ...) is user-installed through the
# package proxy. The version floors patch CVEs in the base; drop them once
# scipy-notebook ships the fixes.
RUN mamba install -y -n base \
    trino-python-client \
    voila \
    jupyter-server-proxy \
    jupyter-vscode-proxy \
    jupyterlab-nvdashboard \
    jupyter-resource-usage \
    nb_conda_kernels \
    'gitpython>=3.1.59' \
    'jupyterlab>=4.6.4' \
    'notebook>=7.6.3' \
    'urllib3>=2.8.0' \
    'certifi>=2026.10.19' \
    'pip>=26.2.2' \
    'tornado>=6.5.9' \
    'jupyter_server>=2.21.0' \
    'pyjwt>=2.14.0' \
    'brotli-python>=1.2.1' \
    'py-rattler>=0.28.1' \
    'oauthlib>=4.0.1' \
    && mamba clean -afy \
    && fix-permissions "${CONDA_DIR}" \
    && fix-permissions "/home/${NB_USER}"

# sdk/python is outside this directory, so the image builds from the repo root.
# setuptools writes egg-info into the source tree, so the build user must own it.
COPY --chown=${NB_UID}:${NB_GID} sdk/python /tmp/scout-sdk
RUN pip install --no-cache-dir /tmp/scout-sdk && rm -rf /tmp/scout-sdk

# The CLI is root-owned, and the pod may have no route to Anthropic's update
# and telemetry endpoints.
ENV DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
