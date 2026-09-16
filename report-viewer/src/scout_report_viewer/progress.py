"""Live Trino query progress for the SPA's loading indicator.

In-process and best-effort: a restart or a second replica is a cache miss.
"""

from __future__ import annotations

import threading
import time
from typing import Any

# Finished entries keep their final totals, so this sweep is the only cleanup.
_MAX_AGE_SECONDS = 600

_lock = threading.Lock()
_entries: dict[str, dict[str, Any]] = {}

_FIELDS = (
    "state",
    "queued",
    "processedRows",
    "processedBytes",
    "progressPercentage",
)


def report(key: str, stats: dict[str, Any]) -> None:
    """Called from the Trino worker thread, so it must not block or await."""
    now = time.time()
    trimmed = {k: stats[k] for k in _FIELDS if k in stats}
    with _lock:
        for stale in [
            k for k, v in _entries.items() if now - v["at"] > _MAX_AGE_SECONDS
        ]:
            del _entries[stale]
        _entries[key] = {"at": now, "stats": trimmed}


def get(key: str) -> dict[str, Any] | None:
    with _lock:
        entry = _entries.get(key)
        return dict(entry["stats"]) if entry else None


def finish(key: str) -> None:
    """Keeps the terminal stats: the SPA's last poll of a running query is up to
    one poll interval short of the real totals."""
    with _lock:
        entry = _entries.get(key)
        if entry:
            entry["stats"]["done"] = True
