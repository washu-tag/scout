"""Domain data for the reconcile."""

from dataclasses import dataclass, field

from .compose import FragmentEffect

INSTALLED, INVALID, REJECTED, RETRACTING = (
    "installed",
    "invalid",
    "rejected",
    "retracting",
)

PROBLEMS = (INVALID, REJECTED)

# `Holding` and `Refused` both leave the realm alone: a retraction waiting out
# its grace period, and discovery never having reported a sync.
PENDING, APPLIED, FAILED, HOLDING, REFUSED = (
    "Pending",
    "Applied",
    "Failed",
    "Holding",
    "Refused",
)

APPLY_JOB_LABEL = "appmanager.scout.xnat.org/role"


@dataclass
class FragmentStatus:
    ref: str
    namespace: str
    name: str
    status: str
    content_hash: str
    display_name: str = ""
    errors: list[str] = field(default_factory=list)
    effect: FragmentEffect | None = None
    applied_at: str | None = None
    # When it was first seen absent. Persisted, so a restart does not hand a
    # vanished fragment a fresh grace period.
    retracting_since: str | None = None


@dataclass
class State:
    fragments: list[FragmentStatus] = field(default_factory=list)
    last_reconcile: str = "never"
    last_applied_hash: str | None = None
    last_result: str = "not yet reconciled"
    pending_change: bool = False
    base_hash: str | None = None
    # sha256 of the base realm document's *bytes*, where base_hash is over the
    # canonicalised parse. A deploy can compute this one, so it is what an
    # `until:` waits on to know the document it just published has applied.
    base_source_hash: str | None = None
    composed_hash: str | None = None
    # A digest over the resourceVersions of every Secret the apply reads. The
    # document no longer moves when a credential is rotated, so this is what
    # tells one apply from the next.
    secrets_version: str | None = None
    applied_secrets_version: str | None = None
    # config-cli's own record of the last document imported into the realm:
    # what it read back after our apply, and what the realm says now. Unequal
    # means another writer, which hash comparison alone cannot see.
    applied_import_checksum: str | None = None
    live_checksum: str | None = None
    drift: bool = False
    identical_to_base: bool = True
    phase: str = PENDING
    applied_at: str | None = None
    # Until discovery has synced, an absent fragment cannot be told apart from
    # an unsynced one, so nothing is retracted.
    discovery_synced: bool = False
    # Readiness; a fact about the realm, so it is restored on startup.
    base_realm_applied: bool = False
