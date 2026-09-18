# The Keycloak realm fragment reconciler (ADR 0037).
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

# Debian security patches lag this base image tag; pull them in explicitly
# until upstream rebuilds. No-op once the base image catches up.
RUN apt-get update && apt-get install -y --no-install-recommends --only-upgrade \
        perl-base libsqlite3-0 gzip libpcre2-8-0 \
    && rm -rf /var/lib/apt/lists/*

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
ENTRYPOINT ["keycloak-fragment-reconciler"]
