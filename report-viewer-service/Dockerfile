FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps first so layer caches survive source edits.
COPY pyproject.toml /app/
COPY src /app/src
RUN pip install .

EXPOSE 8000

# Drop root.
RUN groupadd --system searchs && useradd --system --gid searchs --home /app searchs
USER searchs

CMD ["python", "-m", "scout_report_viewer"]
