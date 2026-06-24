# Stage 1: build the Vite SPA. Output lands at
# /work/src/scout_report_viewer/static (vite.config.ts writes to
# `../src/scout_report_viewer/static` relative to the frontend dir).
FROM node:25-alpine AS frontend
WORKDIR /work
COPY frontend/package.json frontend/package-lock.json frontend/
RUN cd frontend && npm ci
COPY frontend frontend
RUN mkdir -p src/scout_report_viewer && cd frontend && npm run build

# Stage 2: Python runtime.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml /app/
COPY src /app/src
COPY --from=frontend /work/src/scout_report_viewer/static /app/src/scout_report_viewer/static
RUN pip install .

EXPOSE 8000

RUN groupadd --system searchs && useradd --system --gid searchs --home /app searchs
USER searchs

CMD ["python", "-m", "scout_report_viewer"]
