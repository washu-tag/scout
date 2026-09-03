"""The reconciler's status, published to a ConfigMap.

The operator's view of what reached the realm, and the reconciler's durable
memory across a restart: a vanished fragment's grace clock and whether the base
realm has ever been applied. Shaped like a status subresource so a future CRD is
a rename.
"""

import logging
import time
from calendar import timegm

from . import yamlio
from .k8s import ApiError, Client
from .models import INSTALLED, RETRACTING, FragmentStatus, State

log = logging.getLogger("app-manager")

STATUS_KEY = "status"

STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def now() -> str:
    return time.strftime(STAMP_FORMAT, time.gmtime())


def epoch(stamp: str | None) -> float:
    """`stamp` as seconds since the epoch, or 0 if it cannot be read.

    `timegm`, not `mktime`: the stamps are UTC.
    """
    if not stamp:
        return 0.0
    try:
        return float(timegm(time.strptime(stamp, STAMP_FORMAT)))
    except ValueError:
        log.warning("unparseable timestamp %r in the status document", stamp)
        return 0.0


def age_seconds(stamp: str) -> float:
    """How long ago `stamp` was; 0 if unreadable, so it never retracts anything."""
    parsed = epoch(stamp)
    if not parsed:
        return 0.0
    return max(0.0, time.time() - parsed)


def to_document(state: State) -> dict:
    return {
        "observedBaseHash": state.base_hash,
        "composedHash": state.composed_hash,
        "observedSecretsVersion": state.secrets_version,
        "identicalToBase": state.identical_to_base,
        "phase": state.phase,
        "appliedAt": state.applied_at,
        "appliedHash": state.last_applied_hash,
        "appliedSecretsVersion": state.applied_secrets_version,
        "appliedImportChecksum": state.applied_import_checksum,
        "liveImportChecksum": state.live_checksum,
        "driftDetected": state.drift,
        "lastResult": state.last_result,
        "lastReconcile": state.last_reconcile,
        "applyMode": state.apply_mode,
        "discoverySynced": state.discovery_synced,
        "baseRealmApplied": state.base_realm_applied,
        "fragments": [
            {
                "ref": f.ref,
                "namespace": f.namespace,
                "name": f.name,
                "displayName": f.display_name,
                "state": f.status,
                "contentHash": f.content_hash,
                "appliedAt": f.applied_at,
                "retractingSince": f.retracting_since,
                "errors": list(f.errors),
            }
            for f in sorted(state.fragments, key=lambda f: f.ref)
        ],
    }


def from_document(doc: dict) -> State:
    """Rebuild the durable half of the state. `effect` is recomputed, not stored."""
    state = State(
        last_applied_hash=doc.get("appliedHash"),
        last_result=doc.get("lastResult", "not yet reconciled"),
        last_reconcile=doc.get("lastReconcile", "never"),
        base_hash=doc.get("observedBaseHash"),
        composed_hash=doc.get("composedHash"),
        secrets_version=doc.get("observedSecretsVersion"),
        applied_secrets_version=doc.get("appliedSecretsVersion"),
        # Durable: without it a restart has no expectation to compare the live
        # realm against, and drift goes unnoticed until the next apply.
        applied_import_checksum=doc.get("appliedImportChecksum"),
        # Observations rather than memory -- a reconcile overwrites both before
        # reading them. They round-trip because `status` renders this document
        # and would otherwise report a readable realm as unreadable.
        live_checksum=doc.get("liveImportChecksum"),
        drift=bool(doc.get("driftDetected")),
        identical_to_base=bool(doc.get("identicalToBase")),
        phase=doc.get("phase", State.phase),
        applied_at=doc.get("appliedAt"),
        # Parsed because `status` displays it; a resuming service resets it.
        discovery_synced=bool(doc.get("discoverySynced")),
        base_realm_applied=bool(doc.get("baseRealmApplied")),
    )
    for entry in doc.get("fragments") or []:
        ref = entry.get("ref") or ""
        state.fragments.append(
            FragmentStatus(
                ref=ref,
                namespace=entry.get("namespace") or ref.partition("/")[0],
                name=entry.get("name") or ref.partition("/")[2],
                status=entry.get("state") or "",
                content_hash=entry.get("contentHash") or "",
                display_name=entry.get("displayName") or "",
                errors=list(entry.get("errors") or []),
                applied_at=entry.get("appliedAt"),
                retracting_since=entry.get("retractingSince"),
            )
        )
    return state


def render(state: State) -> str:
    return yamlio.safe_dump(
        to_document(state), sort_keys=False, default_flow_style=False
    )


class StatusStore:
    def __init__(self, client: Client, namespace: str, name: str) -> None:
        self.client = client
        self.namespace = namespace
        self.name = name

    def load(self) -> State | None:
        """The last published status, or None on a first install."""
        try:
            configmap = self.client.get_configmap(self.namespace, self.name)
        except ApiError:
            log.exception("could not read status ConfigMap %s", self.name)
            return None
        if not configmap:
            return None
        raw = (configmap.get("data") or {}).get(STATUS_KEY)
        if not raw:
            return None
        try:
            doc = yamlio.safe_load(raw)
        except yamlio.YAMLError:
            log.exception("status ConfigMap %s is not valid YAML; ignoring", self.name)
            return None
        if not isinstance(doc, dict):
            return None
        return from_document(doc)

    def save(self, state: State) -> None:
        """Publish the status. A failure here must not stop a reconcile."""
        try:
            self.client.put_configmap_data(
                self.namespace,
                self.name,
                {STATUS_KEY: render(state)},
                labels={
                    "app.kubernetes.io/name": "scout-app-manager",
                    "app.kubernetes.io/managed-by": "app-manager",
                },
            )
        except ApiError:
            log.exception("could not publish status ConfigMap %s", self.name)


def prior_installed(state: State) -> dict[str, FragmentStatus]:
    """Fragments that reached the realm; `invalid`/`rejected` never did."""
    return {f.ref: f for f in state.fragments if f.status in (INSTALLED, RETRACTING)}
