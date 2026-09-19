"""Prometheus exposition, rendered from the last pass at scrape time.

A custom collector rather than counters kept in step with the reconcile: the
state is a snapshot, every series is a fact about one, and reading it at scrape
time cannot fall behind.

Two series are worth knowing. `writes_total` should stop advancing once the
realm matches the fragments. `drift_repairs_total` is the alerting one -- see
`core._reconcile_tier_edges`.
"""

from collections.abc import Iterator

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric

from .core import APPLIED, FAILED, INVALID, REJECTED, Reconciler

PREFIX = "scout_keycloak_fragment_reconciler"
STATES = (APPLIED, INVALID, REJECTED, FAILED)

__all__ = ["CONTENT_TYPE_LATEST", "PREFIX", "STATES", "render"]


class ReconcilerCollector:
    def __init__(self, reconciler: Reconciler) -> None:
        self.reconciler = reconciler

    def collect(self) -> Iterator[Metric]:
        r = self.reconciler
        snapshot = r.snapshot

        counts = {name: 0 for name in STATES}
        for outcome in snapshot.outcomes:
            if outcome.status in counts:
                counts[outcome.status] += 1
        # Every state, including the ones at zero: a missing series and a zero
        # series alert differently.
        totals = GaugeMetricFamily(
            f"{PREFIX}_clients",
            "Clients a fragment asked for, by outcome of the last pass.",
            labels=["state"],
        )
        for name in STATES:
            totals.add_metric([name], counts[name])
        yield totals

        # `document` is here to keep the label set unique, not for decoration:
        # a document that failed to parse has no clientId to report, so two
        # broken documents in one ConfigMap would otherwise be the same series
        # twice. `client_id` stays empty in that case rather than borrowing the
        # document name, so a query by client_id cannot match a document.
        per_client = GaugeMetricFamily(
            f"{PREFIX}_client_state",
            "One per client a fragment asked for, labelled with its outcome. "
            "client_id is empty when the document did not parse.",
            labels=["source", "client_id", "document", "state"],
        )
        for outcome in sorted(
            snapshot.outcomes, key=lambda o: (o.source, o.client_id, o.document)
        ):
            per_client.add_metric(
                [outcome.source, outcome.client_id, outcome.document, outcome.status], 1
            )
        yield per_client

        for name, help_text, value in [
            (
                "tier_roles_present",
                "1 when every configured tier realm role exists. 0 means the "
                "base realm has not been applied and nothing can be granted.",
                float(r.tiers_present),
            ),
            (
                "last_successful_list_timestamp_seconds",
                "When fragments were last listed successfully. GC is skipped "
                "on any cycle where this does not advance.",
                r.last_list_ok,
            ),
            (
                "orphans_pending",
                "Stamped clients whose fragment is absent and whose grace "
                "period has not yet elapsed.",
                float(len(r.first_absent_at)),
            ),
        ]:
            yield GaugeMetricFamily(f"{PREFIX}_{name}", help_text, value=value)

        for name, help_text, value in [
            (
                "writes_total",
                "Keycloak writes issued since start. A second pass over "
                "unchanged fragments must not advance this.",
                float(r.writes),
            ),
            (
                "deletions_total",
                "Fragment clients deleted after their grace period.",
                float(r.deletions),
            ),
            (
                "drift_repairs_total",
                "Tier edges re-added to a client that already existed. A "
                "nonzero value usually means the base realm's tier roles "
                "gained a `composites` key and are reaping fragment grants.",
                float(r.drift_repairs),
            ),
        ]:
            yield CounterMetricFamily(f"{PREFIX}_{name}", help_text, value=value)


def render(reconciler: Reconciler) -> str:
    """One scrape's worth of exposition text."""
    registry = CollectorRegistry()
    registry.register(ReconcilerCollector(reconciler))
    return generate_latest(registry).decode("utf-8")
