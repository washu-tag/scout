"""Prometheus /metrics: HTTP RED via prometheus-fastapi-instrumentator plus
a handful of custom domain counters/histograms."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator

from fastapi import FastAPI
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_fastapi_instrumentator import metrics as fastapi_metrics

log = logging.getLogger(__name__)

SEARCHES_CREATED = Counter(
    "scout_report_viewer_searches_created_total",
    "Searches saved via POST /searches.",
)

TRINO_QUERY_DURATION = Histogram(
    "scout_report_viewer_trino_query_duration_seconds",
    "Time spent in Trino per call from the report-viewer service.",
    ["op", "result"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0),
)

POSTGRES_OP_DURATION = Histogram(
    "scout_report_viewer_postgres_op_duration_seconds",
    "Time spent in Postgres per search CRUD op.",
    ["op", "result"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
)

# Log-ish buckets: most searches are hundreds of rows, a few are millions.
_ROW_COUNT_BUCKETS = (
    10,
    100,
    500,
    1_000,
    5_000,
    10_000,
    50_000,
    100_000,
    500_000,
    1_000_000,
)

SEARCH_SIZE = Histogram(
    "scout_report_viewer_search_size_rows",
    "Row count of each search, from the COUNT(*) computed at create time.",
    buckets=_ROW_COUNT_BUCKETS,
)

# Rows actually returned to the client per read, vs SEARCH_SIZE's create-time
# count: this shows live fetch volume (how many rows each iframe/tool load ships
# and, via _sum, total rows served per second). Same buckets as SEARCH_SIZE so
# the two are directly comparable.
RESULT_ROWS = Histogram(
    "scout_report_viewer_result_rows",
    "Rows returned to the client per read op (payload size, not cohort COUNT).",
    ["op"],
    buckets=_ROW_COUNT_BUCKETS,
)


@contextmanager
def time_trino(op: str) -> Iterator[None]:
    # Hand-rolled instead of Histogram.time() so failures don't count as ok:
    # `.time()` observes on __exit__ regardless of exception.
    start = time.monotonic()
    outcome = "ok"
    try:
        yield
    except Exception:
        outcome = "error"
        raise
    finally:
        TRINO_QUERY_DURATION.labels(op=op, result=outcome).observe(
            time.monotonic() - start
        )


@contextmanager
def time_postgres(op: str) -> Iterator[None]:
    start = time.monotonic()
    outcome = "ok"
    try:
        yield
    except Exception:
        outcome = "error"
        raise
    finally:
        POSTGRES_OP_DURATION.labels(op=op, result=outcome).observe(
            time.monotonic() - start
        )


def install(app: FastAPI) -> None:
    instrumentator = Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/healthz", "/readyz", "/metrics"],
    )
    # The library's default per-handler latency buckets are (0.1, 0.5, 1), which
    # clamps every per-endpoint p50/p95 to <=1s. Re-add the default group with
    # buckets that reach the Trino range so slow handlers (e.g. rows_query) read
    # true. Status grouping still applies - the middleware sets modified_status.
    instrumentator.add(
        fastapi_metrics.default(
            latency_lowr_buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
        )
    )
    instrumentator.instrument(app).expose(
        app, endpoint="/metrics", include_in_schema=False, tags=["meta"]
    )
    log.info("prometheus /metrics endpoint mounted")
    return instrumentator
