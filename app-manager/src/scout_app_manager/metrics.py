"""Prometheus exposition, rendered from a State.

A custom collector rather than module-level Gauges kept in step with the
reconcile: a State is a snapshot, every series here is a fact about the last
one, and reading it at scrape time is the shape that cannot fall behind.
"""

from collections.abc import Iterator

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily, Metric

from .status import INSTALLED, INVALID, REJECTED, RETRACTING, State, epoch

STATES = (INSTALLED, INVALID, REJECTED, RETRACTING)

PREFIX = "scout_app_manager"

__all__ = ["CONTENT_TYPE_LATEST", "PREFIX", "STATES", "epoch", "render"]


def _realm_facts(state: State) -> list[tuple[str, str, float]]:
    """The single-sample gauges: name, help, value."""
    return [
        (
            "base_realm_applied",
            "1 when the base realm has been applied at least once.",
            float(state.base_realm_applied),
        ),
        (
            "discovery_synced",
            "1 when the discovery sidecar has reported a complete initial sync.",
            float(state.discovery_synced),
        ),
        (
            "last_apply_success_timestamp_seconds",
            "When the realm was last applied successfully.",
            epoch(state.applied_at),
        ),
        (
            "apply_pending",
            "1 when the composed realm differs from what was last applied.",
            float(state.pending_change),
        ),
        (
            "realm_drift",
            "1 when the live realm was last written by something other than "
            "this reconciler.",
            float(state.drift),
        ),
        (
            "realm_checksum_readable",
            "1 when the live realm's import checksum could be read from "
            "Keycloak. 0 means drift cannot be detected at all.",
            float(bool(state.live_checksum)),
        ),
        (
            "realm_unmanaged",
            "1 when the realm was read and is either gone or has never been "
            "imported into. The reconciler is unready while it is.",
            float(state.realm_unmanaged),
        ),
    ]


class StateCollector:
    def __init__(self, state: State) -> None:
        self.state = state

    def collect(self) -> Iterator[Metric]:
        state = self.state

        counts = {name: 0 for name in STATES}
        for fragment in state.fragments:
            if fragment.status in counts:
                counts[fragment.status] += 1
        # Every state, including the ones at zero: a missing series and a zero
        # series alert differently.
        totals = GaugeMetricFamily(
            f"{PREFIX}_fragments", "Discovered fragments by state.", labels=["state"]
        )
        for name in STATES:
            totals.add_metric([name], counts[name])
        yield totals

        per_fragment = GaugeMetricFamily(
            f"{PREFIX}_fragment_state",
            "One per fragment, labelled with its state.",
            labels=["namespace", "name", "state"],
        )
        for fragment in sorted(state.fragments, key=lambda f: f.ref):
            per_fragment.add_metric(
                [fragment.namespace, fragment.name, fragment.status], 1
            )
        yield per_fragment

        for name, help_text, value in _realm_facts(state):
            yield GaugeMetricFamily(f"{PREFIX}_{name}", help_text, value=value)

        phase = GaugeMetricFamily(
            f"{PREFIX}_phase",
            "The reconcile's last outcome, as a label.",
            labels=["phase"],
        )
        phase.add_metric([state.phase], 1)
        yield phase


def render(state: State) -> str:
    """One scrape's worth of exposition text."""
    registry = CollectorRegistry()
    registry.register(StateCollector(state))
    return generate_latest(registry).decode("utf-8")
