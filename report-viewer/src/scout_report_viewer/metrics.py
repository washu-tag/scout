"""Prometheus metrics — exposes /metrics + a few custom domain counters.

We use `prometheus-fastapi-instrumentator` for the standard HTTP histograms
(request count, latency, in-flight) and `prometheus-client` directly for
the search-specific counters that the LLM-bound and viewer paths care
about. The Scout Prometheus instance picks /metrics up via an explicit
scrape job in `ansible/roles/prometheus/templates/values.yaml.j2`,
gated on `enable_report_viewer` in inventory.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from fastapi import FastAPI
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

log = logging.getLogger(__name__)


# Custom domain metrics. Label cardinality is bounded on purpose:
#   - `kind` ∈ {"report", "patient"} (~2)
#   - `id_column` ∈ {"message_control_id", "accession_number", "epic_mrn", "scout_patient_id"} (~4)
#   - `op` ∈ {"create_query", "rows_query", "summary_query"} (~3)
#   - `result` ∈ {"ok", "error"} (~2)
# Anything user-derived (owner_sub, search_id) stays OUT of labels.

SEARCHES_CREATED = Counter(
    "scout_report_viewer_searches_created_total",
    "Searches saved via POST /searches.",
    ["kind", "id_column", "result"],
)

TRINO_QUERY_DURATION = Histogram(
    "scout_report_viewer_trino_query_duration_seconds",
    "Time spent in Trino per call from the report-viewer service.",
    ["op", "result"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

POSTGRES_OP_DURATION = Histogram(
    "scout_report_viewer_postgres_op_duration_seconds",
    "Time spent in Postgres per search CRUD op.",
    ["op", "result"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
)

# Search row-count distribution — wide log-ish buckets so we can see "most
# searches are ~hundreds, a few are big". Recorded once per successful
# create_search / create_from_file.
SEARCH_SIZE = Histogram(
    "scout_report_viewer_search_size_rows",
    "Row count of each search at create time (cached COUNT(*) of the saved SQL).",
    ["kind", "source"],  # source ∈ {"sql", "from_file"}
    buckets=(10, 100, 500, 1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000),
)

# Identity-import counters. Surface matched + unmatched separately so a
# dashboard can show the unmatched-rate per import.
IDS_SUBMITTED = Counter(
    "scout_report_viewer_ids_submitted_total",
    "Total identifiers submitted via /api/searches/from-file.",
    ["id_column"],
)

IDS_UNMATCHED = Counter(
    "scout_report_viewer_ids_unmatched_total",
    "Identifiers submitted but not found in reports_latest.",
    ["id_column"],
)

# OWUI new-user webhook activity. Useful for "did the webhook fire?"
# debugging without spelunking through logs.
OWUI_WEBHOOK_EVENTS = Counter(
    "scout_report_viewer_owui_webhook_events_total",
    "OWUI admin-notification webhook events received.",
    ["action", "result"],  # action: signup|other; result: enabled|skipped|error
)

# /api/searches/{id}/reports/{report_id} lazy fetch — separate counter
# so we can see how much row-expand traffic the SPA generates and tune
# cache behavior.
REPORT_FETCH = Counter(
    "scout_report_viewer_report_fetch_total",
    "On-the-fly full-report fetches from the row-expand panel.",
    ["result"],  # ok | not_found | error
)


@contextmanager
def time_trino(op: str) -> Iterator[None]:
    """Time a Trino query and record op+result. Failures still record so
    error-rate dashboards can compare with success counts."""
    with TRINO_QUERY_DURATION.labels(op=op, result="ok").time():
        try:
            yield
        except Exception:
            # Re-label the just-observed sample as error. Cheaper than
            # cancelling the context manager's sample: we observe twice
            # (once on the ok path's `time()`, but on exception that ctx
            # was never entered) — so only this error path increments.
            TRINO_QUERY_DURATION.labels(op=op, result="error").observe(0.0)
            raise


@contextmanager
def time_postgres(op: str) -> Iterator[None]:
    with POSTGRES_OP_DURATION.labels(op=op, result="ok").time():
        try:
            yield
        except Exception:
            POSTGRES_OP_DURATION.labels(op=op, result="error").observe(0.0)
            raise


def install(app: FastAPI) -> None:
    """Mount /metrics and the standard HTTP request instrumentator.

    Idempotent — the instrumentator only attaches once per app instance.
    """
    instrumentator = (
        Instrumentator(
            should_group_status_codes=True,
            should_ignore_untemplated=True,
            excluded_handlers=["/healthz", "/metrics"],
            inprogress_name="scout_report_viewer_http_requests_in_progress",
            inprogress_labels=True,
        )
        .instrument(app)
        .expose(app, endpoint="/metrics", include_in_schema=False, tags=["meta"])
    )
    log.info("prometheus /metrics endpoint mounted")
    return instrumentator
