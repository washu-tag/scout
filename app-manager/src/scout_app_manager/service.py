"""Business logic. The CLI and the reconcile loop are both thin callers of this.

Anything that decides something lives here; cli.py prints, loop.py schedules.
`compose` is the single arbiter of what reaches the realm -- this module only
labels what it decided, so the CLI's report and the applied realm cannot
disagree.

Retraction is damped, because a vanished fragment is indistinguishable from one
mid-redeploy: absence suppresses the apply until the grace period has run, and
never retracts unless discovery has reported a complete sync.
"""

import logging
from dataclasses import replace
from pathlib import Path

from .apply import RealmApplier
from .compose import ComposeResult, Site, canonical, compose, plan, realm_hash
from .k8s import ApiError, Client
from .load import LoadedFragment, parse_realm_document, scan
from .models import (
    APPLIED,
    FAILED,
    HOLDING,
    INSTALLED,
    INVALID,
    PENDING,
    PROBLEMS,
    REFUSED,
    REJECTED,
    RETRACTING,
    FragmentStatus,
    State,
)
from .settings import Settings
from .status import StatusStore, age_seconds, now, prior_installed

log = logging.getLogger("app-manager")


def display_name_for(item: LoadedFragment) -> str:
    """A human label for the app a fragment installs.

    Derived from the primary client's declared display name; the ConfigMap name
    is the fallback, with the conventional `-keycloak` suffix dropped.
    """
    if item.fragment and item.fragment.clients:
        primary = item.fragment.clients[0]
        if primary.displayName:
            return primary.displayName
    name = item.ref.name
    for suffix in ("-keycloak", "-keycloak-fragment", "-fragment"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


class AppManagerService:
    def __init__(
        self,
        settings: Settings,
        client: Client,
        applier: RealmApplier | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.namespace = settings.namespace or client.namespace()
        self.applier = applier or RealmApplier(settings, client, self.namespace)
        self.store = StatusStore(client, self.namespace, settings.status_configmap)
        self.state = self._resume()
        self.reconciles = 0

    def _resume(self) -> State:
        """Pick up where the last process left off, or start clean."""
        restored = self.store.load()
        if restored is None:
            log.info("no previous status found; starting from an empty state")
            return State(apply_mode=self.settings.apply_mode)
        restored.apply_mode = self.settings.apply_mode
        # A fact about this process: a previous sync says nothing about
        # /fragments now, and trusting it would retract on an empty dir.
        restored.discovery_synced = False
        log.info(
            "resumed: applied=%s baseRealmApplied=%s fragments=%s",
            (restored.last_applied_hash or "-")[:19],
            restored.base_realm_applied,
            len(restored.fragments),
        )
        return restored

    # --- inputs ---------------------------------------------------------

    def site(self) -> Site:
        return Site(domain=self.settings.domain, signout_url=self.settings.signout_url)

    def resolve_secret(self, name: str, key: str) -> str | None:
        try:
            return self.client.get_secret_value(self.namespace, name, key)
        except ApiError:
            log.exception("could not read secret %s/%s", self.namespace, name)
            return None

    def base_realm(self) -> dict:
        raw = parse_realm_document(
            Path(self.settings.base_realm_path).read_text(encoding="utf-8")
        )
        return raw.get("realm_representation", raw)

    def ready(self) -> bool:
        """Readiness: the base realm has been applied. Never about fragments.

        Diff mode writes no realm, so there it can only mean the reconcile runs.
        """
        if self.settings.apply_mode == "apply":
            return self.state.base_realm_applied
        return self.reconciles > 0

    def next_deadline(self) -> float | None:
        """Seconds until a held retraction's grace period runs out, if any.

        The loop waits on this so the configured grace is the actual delay.
        """
        waits = [
            self.settings.retraction_grace_seconds - age_seconds(f.retracting_since)
            for f in self.state.fragments
            if f.status == RETRACTING and f.retracting_since
        ]
        return max(0.0, min(waits)) if waits else None

    # --- the reconcile ---------------------------------------------------

    def reconcile_once(self) -> State:
        state = self._reconcile()
        self.reconciles += 1
        return state

    def _reconcile(self) -> State:
        prior = prior_installed(self.state)
        loaded = scan(self.settings.fragment_dir)
        base = self.base_realm()
        result = compose(base, loaded, self.site(), self.resolve_secret)

        reasons = {str(item.ref): why for item, why in result.rejected}
        statuses = [
            self._status(item, reasons.get(str(item.ref)), prior) for item in loaded
        ]
        held = self._retracting(prior, {s.ref for s in statuses})
        statuses.extend(held)

        self._log_decisions(statuses)
        desired_hash = realm_hash(result.realm)
        base_hash = realm_hash(base)

        self.state.fragments = statuses
        self.state.last_reconcile = now()
        self.state.base_hash = base_hash
        self.state.composed_hash = desired_hash
        self.state.identical_to_base = desired_hash == base_hash
        self.state.pending_change = desired_hash != self.state.last_applied_hash

        if held:
            return self._hold(held)
        if not self.state.pending_change:
            self.state.phase = APPLIED if self.state.last_applied_hash else PENDING
            self.state.last_result = (
                f"realm is up to date at {(self.state.last_applied_hash or '-')[:19]}"
            )
            self.store.save(self.state)
            return self.state
        if self.settings.apply_mode != "apply":
            return self._report_diff(result, base_hash, desired_hash)

        self.state.phase = PENDING
        self.store.save(self.state)
        self._apply(result, desired_hash)
        self.store.save(self.state)
        return self.state

    def _status(
        self,
        item: LoadedFragment,
        reasons: list[str] | None,
        prior: dict[str, FragmentStatus],
    ) -> FragmentStatus:
        was = prior.get(str(item.ref))
        status = FragmentStatus(
            ref=str(item.ref),
            namespace=item.ref.namespace,
            name=item.ref.name,
            status=INSTALLED,
            content_hash=item.content_hash,
            display_name=display_name_for(item),
            errors=list(item.errors),
            # When it last reached the realm, not when it was last looked at.
            applied_at=was.applied_at if was else None,
        )
        if not item.valid:
            # compose rejected it too, for the same reasons it already carries.
            status.status = INVALID
            return status
        status.effect = plan(item, self.site())
        if reasons is not None:
            status.status = REJECTED
            status.errors.extend(reasons)
        return status

    def _retracting(
        self, prior: dict[str, FragmentStatus], present: set[str]
    ) -> list[FragmentStatus]:
        """Fragments that were in the realm and are no longer on disk.

        A returned entry holds the apply; a dropped one is a retraction.
        """
        held = []
        for ref, was in sorted(prior.items()):
            if ref in present:
                continue
            since = was.retracting_since or now()
            waited = age_seconds(since)
            expired = waited >= self.settings.retraction_grace_seconds
            if expired and self.state.discovery_synced:
                log.warning(
                    "RETRACTED %s: absent for %ds, removing its realm objects",
                    ref,
                    int(waited),
                )
                continue
            # No elapsed time: nothing reconciles during a hold, so this string
            # sits frozen in the status document until the grace period ends.
            reason = (
                "discovery has not reported a complete sync, so an absent "
                "fragment cannot be told apart from an unsynced one"
                if not self.state.discovery_synced
                else f"absent since {since}; its realm objects are retracted if "
                f"it has not returned {self.settings.retraction_grace_seconds}s later"
            )
            log.warning("HOLDING   %s: absent for %ds", ref, int(waited))
            held.append(
                replace(was, status=RETRACTING, retracting_since=since, errors=[reason])
            )
        return held

    def _hold(self, held: list[FragmentStatus]) -> State:
        """Leave the realm exactly as it is, and say why."""
        refused = not self.state.discovery_synced
        self.state.phase = REFUSED if refused else HOLDING
        self.state.last_result = (
            f"holding: {len(held)} fragment(s) absent but not yet retracted"
            + (" (discovery never synced)" if refused else "")
        )
        log.warning("HOLDING   the realm is unchanged: %s", self.state.last_result)
        self.store.save(self.state)
        return self.state

    def _report_diff(
        self, result: ComposeResult, base_hash: str, desired_hash: str
    ) -> State:
        if self.state.identical_to_base:
            summary = "composed realm is byte-identical to the base realm"
        else:
            summary = (
                f"{len(result.accepted)} fragment(s) would change the realm "
                f"({base_hash[:19]} -> {desired_hash[:19]})"
            )
        log.info("DIFF MODE: not applying. %s", summary)
        self.state.last_result = f"diff mode: {summary}"
        self.state.phase = PENDING
        self.store.save(self.state)
        return self.state

    def _apply(self, result: ComposeResult, desired_hash: str) -> None:
        self.applier.publish(canonical(result.realm))
        ok, detail = self.applier.run(desired_hash)
        if not ok:
            log.error("APPLY     FAILED for realm %s: %s", desired_hash[:19], detail)
            self.state.phase = FAILED
            self.state.last_result = f"apply failed: {detail}"
            return
        log.info("APPLY     succeeded; realm is now %s", desired_hash[:19])
        stamp = now()
        self.state.last_applied_hash = desired_hash
        self.state.pending_change = False
        self.state.phase = APPLIED
        self.state.applied_at = stamp
        self.state.base_realm_applied = True
        self.state.last_result = (
            f"applied {desired_hash[:19]} "
            f"({len(result.accepted)} fragment(s) composed)"
        )
        for fragment in self.state.fragments:
            if fragment.status == INSTALLED:
                fragment.applied_at = stamp

    def _log_decisions(self, statuses: list[FragmentStatus]) -> None:
        for status in statuses:
            if status.status in PROBLEMS:
                log.warning(
                    "EXCLUDED  %s: %s",
                    status.ref,
                    "; ".join(status.errors) or status.status,
                )
            elif status.status == INSTALLED:
                log.info("INSTALLED %s", status.ref)
