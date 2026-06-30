"""Structured JSON logging to stdout (Loki picks it up via Promtail).

We deliberately use stdlib `logging` + a tiny JSON formatter rather than
pulling in `structlog` / `python-json-logger`. The set of fields we need
is small and stable, and one fewer dependency keeps the test-mode
source-mount image's startup pip install fast.

Every log line carries: timestamp (ISO-8601 UTC), level, logger, message,
exception info if any, and a `service` field to disambiguate in Loki when
multiple Scout pods share a namespace.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone


_SERVICE_LABEL = "scout-report-viewer"


class JsonFormatter(logging.Formatter):
    """Emit each record as a single JSON line.

    Fields beyond the standard set come from `record.__dict__` keys that
    aren't part of the stdlib LogRecord attribute set - i.e. anything the
    caller passed via `logger.info("msg", extra={"search_id": ...})`.
    """

    _STD_KEYS = frozenset(
        {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
            "taskName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "service": _SERVICE_LABEL,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Extras: anything the caller attached via `extra=` lands as a top-
        # level key (e.g. search_id, user_sub, request_id, duration_ms).
        for key, value in record.__dict__.items():
            if key in self._STD_KEYS or key.startswith("_"):
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = repr(value)
            payload[key] = value
        return json.dumps(payload, separators=(",", ":"))


def configure(level: str = "INFO") -> None:
    """Replace the root handler with a single JSON-on-stdout handler.

    Idempotent - re-running clears the previous handler set so a noisy
    library that called `basicConfig()` doesn't leave duplicate sinks.
    Honors `REPORT_VIEWER_LOG_LEVEL` if set; otherwise uses the passed level.
    """
    effective_level = os.environ.get("REPORT_VIEWER_LOG_LEVEL", level).upper()
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(effective_level)

    # uvicorn ships its own loggers with formatters - clear them so the
    # JSON path is the single source of truth for stdout.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        ulog = logging.getLogger(name)
        ulog.handlers.clear()
        ulog.propagate = True
