"""OpenTelemetry bootstrap — wired but inert unless explicitly enabled.

When `OTEL_EXPORTER_OTLP_ENDPOINT` is set in the environment, this module
installs the FastAPI/httpx/psycopg auto-instrumentors and configures a
batch span exporter pointed at the OTLP HTTP endpoint. With no endpoint
configured, `bootstrap()` is a no-op — no spans created, no batch threads
started, no overhead.

This lets us ship OTel-ready today and flip it on later (when Scout adds
a tracing backend) by setting a couple of env vars in the chart values —
no code changes, no redeploy of the service binary.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI

log = logging.getLogger(__name__)


def _enabled() -> bool:
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


def bootstrap(app: FastAPI) -> None:
    """Install OTel instrumentors if an OTLP endpoint is configured.

    Safe to call once at startup. Imports are deferred so that a deploy
    without the OTel libs available (e.g. an earlier rev of the image)
    doesn't fail at import time — only callers that actually want
    tracing pay the import cost.
    """
    if not _enabled():
        log.info("OpenTelemetry disabled — no OTEL_EXPORTER_OTLP_ENDPOINT set")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception:
        log.exception("OTel imports failed — tracing not enabled")
        return

    # `OTEL_SERVICE_NAME` is the standard env var; default if unset so the
    # Loki / collector side groups our spans under a stable name.
    service_name = os.environ.get("OTEL_SERVICE_NAME", "scout-report-viewer")
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    PsycopgInstrumentor().instrument()

    log.info(
        "OpenTelemetry enabled: service.name=%s endpoint=%s",
        service_name,
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
    )
