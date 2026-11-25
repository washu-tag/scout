# Renders repo2docker's build context for the repo in the main context; see render.py.
FROM python:3.12-slim AS render
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
RUN pip install --no-cache-dir jupyter-repo2docker==2026.4.0
COPY --from=tools . /tools
COPY . /src
ARG CONDA_CHANNEL_ALIAS
ARG APT_MIRROR
ARG APT_SECURITY_MIRROR
RUN python /tools/render.py /src /out

FROM scratch
COPY --from=render /out /
