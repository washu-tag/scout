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

# Dependencies come from pyproject.toml alone, so editing src does not re-resolve
# them. pip check then proves the split left nothing unsatisfied.
RUN python -c "import tomllib,pathlib;pathlib.Path('/tmp/requirements.txt').write_text(chr(10).join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" \
    && pip install -r /tmp/requirements.txt

COPY src ./src
RUN pip install --no-deps . && pip check

USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["keycloak-fragment-reconciler"]
