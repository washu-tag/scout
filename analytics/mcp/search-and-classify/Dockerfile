FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# System deps for wheels that use native bits (usually manylinux wheels suffice, this is light)
RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc curl tini \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
      "uvicorn[standard]" fastapi pydantic \
      trino \
      "transformers>=4.41,<4.46" \
      "torch>=2.3,<2.5"

# Create non-root user
RUN useradd -m -u 10001 appuser

WORKDIR /app

# Copy app
COPY search_and_classify_service.py /app/search_and_classify_service.py

# Optional: pre-create a cache dir in case you want to mount a PVC for models
RUN mkdir -p /app/models && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "search_and_classify_service:app", "--host", "0.0.0.0", "--port", "8000"]
