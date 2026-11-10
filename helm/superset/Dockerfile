# See https://github.com/apache/superset/blob/master/Dockerfile
FROM apache/superset:5.0.0

USER root

# Install system-level dependencies
RUN apt-get update && apt-get install -y \
    python3-dev \
    build-essential \
    pkg-config \
    zip \
    ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/* /var/tmp/*

# Remove this backport once the base includes apache/superset#41173.
RUN sed -i 's/^gunicorn /exec gunicorn /' \
    /usr/bin/run-server.sh /app/docker/entrypoints/run-server.sh \
    && grep -q '^exec gunicorn ' /usr/bin/run-server.sh \
    && grep -q '^exec gunicorn ' /app/docker/entrypoints/run-server.sh

# Install required Python packages using uv
# uv's built-in roots predate the CVE-2026-91403 fix; use the upgraded system store.
RUN UV_NATIVE_TLS=1 uv pip install --no-cache \
    authlib \
    psycopg2-binary \
    pyhive \
    trino \
    pillow \
    prometheus_client \
    statsd \
    'certifi>=2026.10.19'

# Switch to user that runs Superset
USER superset

# Create bootstrap marker file
RUN echo 'Running Superset with pre-built dependencies' > ~/bootstrap
