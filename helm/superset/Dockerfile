# See https://github.com/apache/superset/blob/master/Dockerfile
FROM apache/superset:5.0.0

USER root

# Install system-level dependencies
RUN apt-get update && apt-get install -y \
    python3-dev \
    build-essential \
    pkg-config \
    zip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/* /var/tmp/*

# Install required Python packages using uv
COPY requirements.txt /tmp/requirements.txt
RUN uv pip install --no-cache -r /tmp/requirements.txt

# Switch to user that runs Superset
USER superset

# Create bootstrap marker file
RUN echo 'Running Superset with pre-built dependencies' > ~/bootstrap