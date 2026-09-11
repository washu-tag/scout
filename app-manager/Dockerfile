# The fragment reconciler and its CLI.
ARG BASE_IMAGE=python:3.14-slim
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install .

USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["scout-app-manager-service"]
