"""Prometheus exposition, rendered from a State.

Hand-written text format for the same reason `k8s.py` is a hand-rolled API
client: the vendored wheel set has to build air-gapped.
"""

from .models import INSTALLED, INVALID, REJECTED, RETRACTING, State
from .status import epoch

STATES = (INSTALLED, INVALID, REJECTED, RETRACTING)

PREFIX = "scout_app_manager"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render(state: State) -> str:
    counts = {name: 0 for name in STATES}
    for fragment in state.fragments:
        if fragment.status in counts:
            counts[fragment.status] += 1

    lines = [
        f"# HELP {PREFIX}_fragments Discovered fragments by state.",
        f"# TYPE {PREFIX}_fragments gauge",
    ]
    for name in STATES:
        lines.append(f'{PREFIX}_fragments{{state="{name}"}} {counts[name]}')

    lines += [
        f"# HELP {PREFIX}_fragment_state One per fragment, labelled with its state.",
        f"# TYPE {PREFIX}_fragment_state gauge",
    ]
    for fragment in sorted(state.fragments, key=lambda f: f.ref):
        lines.append(
            f"{PREFIX}_fragment_state{{"
            f'namespace="{_escape(fragment.namespace)}",'
            f'name="{_escape(fragment.name)}",'
            f'state="{_escape(fragment.status)}"'
            f"}} 1"
        )

    for name, help_text, value in (
        (
            "base_realm_applied",
            "1 when the base realm has been applied at least once.",
            int(state.base_realm_applied),
        ),
        (
            "discovery_synced",
            "1 when the discovery sidecar has reported a complete initial sync.",
            int(state.discovery_synced),
        ),
        (
            "last_apply_success_timestamp_seconds",
            "When the realm was last applied successfully.",
            epoch(state.applied_at),
        ),
        (
            "apply_pending",
            "1 when the composed realm differs from what was last applied.",
            int(state.pending_change),
        ),
        (
            "realm_drift",
            "1 when the live realm was last written by something other than "
            "this reconciler.",
            int(state.drift),
        ),
        (
            "realm_checksum_readable",
            "1 when the live realm's import checksum could be read from "
            "Keycloak. 0 means drift cannot be detected at all.",
            int(bool(state.live_checksum)),
        ),
    ):
        lines += [
            f"# HELP {PREFIX}_{name} {help_text}",
            f"# TYPE {PREFIX}_{name} gauge",
            f"{PREFIX}_{name} {value}",
        ]

    lines += [
        f"# HELP {PREFIX}_phase The reconcile's last outcome, as a label.",
        f"# TYPE {PREFIX}_phase gauge",
        f'{PREFIX}_phase{{phase="{_escape(state.phase)}"}} 1',
    ]
    return "\n".join(lines) + "\n"
