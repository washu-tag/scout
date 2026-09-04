"""Business logic. The CLI and the reconcile loop are both thin callers of this.

Anything that decides something lives here; cli.py prints, loop.py schedules.
`compose` is the single arbiter of what reaches the realm -- this module only
labels what it decided, so the CLI's report and the applied realm cannot
disagree.

Retraction is damped, because a vanished fragment is indistinguishable from one
mid-redeploy: absence suppresses the apply until the grace period has run, and
never retracts unless discovery has reported a complete sync.

The realm document names its credentials rather than carrying them, which puts
two obligations here. Every `$(env:...)` it names must resolve to something
non-empty before the apply, because config-cli installs an unresolved token
verbatim as a client secret and nothing errors. And a rotation has to be
noticed some other way, since replacing a credential no longer moves the
document -- that is what the Secrets' resourceVersions are for.
"""

import hashlib
import logging
from dataclasses import replace
from pathlib import Path

from . import substitution
from .apply import RealmApplier
from .compose import (
    ComposeResult,
    Site,
    canonical,
    compose,
    document_hash,
    plan,
    realm_hash,
)
from .k8s import ApiError, Client, value_of, version_of
from .keycloak import KeycloakAdmin
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


def referenced_secrets(loaded: list[LoadedFragment]) -> set[str]:
    """Every Secret a fragment names, whether or not it exists yet.

    Taken from what was loaded rather than from what composed, because a
    missing credential is exactly what gets a fragment rejected -- and the
    fragment landing before its Secret is the case the doorbell is most useful
    for. Binding-derived names would cover everything except it.
    """
    return {
        client.secretRef.name
        for item in loaded
        if item.fragment
        for client in item.fragment.clients
    }


class AppManagerService:
    def __init__(
        self,
        settings: Settings,
        client: Client,
        applier: RealmApplier | None = None,
        keycloak: KeycloakAdmin | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.namespace = settings.namespace or client.namespace()
        self.applier = applier or RealmApplier(settings, client, self.namespace)
        self.keycloak = keycloak or KeycloakAdmin(
            settings.keycloak_url, settings.keycloak_realm, self.admin_credentials
        )
        self.store = StatusStore(client, self.namespace, settings.status_configmap)
        self.state = self._resume()
        self.reconciles = 0
        # One GET per distinct Secret per reconcile. Cleared at the top of each
        # one, so a rotation is seen on the next pass and not a stale value.
        self._secrets: dict[str, dict | None] = {}
        # What the Secret watch should ring the doorbell for. Widened by each
        # reconcile as fragments name their own credentials.
        self._watched_secrets = {settings.client_secrets_secret, settings.admin_secret}

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

    def secret(self, name: str) -> dict | None:
        """A Secret from this namespace, fetched once per reconcile."""
        if name not in self._secrets:
            try:
                self._secrets[name] = self.client.get_secret(self.namespace, name)
            except ApiError:
                log.exception("could not read secret %s/%s", self.namespace, name)
                self._secrets[name] = None
        return self._secrets[name]

    def resolve_secret(self, name: str, key: str) -> str | None:
        return value_of(self.secret(name), key)

    def client_secret_keys(self) -> set[str]:
        """The base realm's substitution variables that actually resolve.

        A key present but empty is treated as absent: config-cli would happily
        substitute the empty string as a client's credential.
        """
        secret = self.secret(self.settings.client_secrets_secret)
        if not secret:
            log.warning(
                "secret %s not found; the base realm's $(env:...) variables "
                "cannot be resolved",
                self.settings.client_secrets_secret,
            )
            return set()
        return {key for key in (secret.get("data") or {}) if value_of(secret, key)}

    def admin_credentials(self) -> tuple[str, str] | None:
        """The same credential the apply Job authenticates with."""
        secret = self.secret(self.settings.admin_secret)
        username = value_of(secret, "username")
        password = value_of(secret, "password")
        if not username or not password:
            return None
        return username, password

    def watched_secrets(self) -> set[str]:
        """The Secrets whose rotation should wake the reconciler.

        Recomputed each reconcile rather than configured, so a fragment needs
        no label on its Secret -- its `secretRef` already names it. A new
        fragment's credential joins the set on the reconcile that composes it,
        which its own ConfigMap event has already triggered.
        """
        return set(self._watched_secrets)

    def watched_configmaps(self) -> set[str]:
        """The base realm, and nothing else. Fragments come by the sidecar."""
        name = self.settings.base_realm_configmap
        return {name} if name else set()

    def secrets_version(self, names: list[str]) -> str:
        """A digest over the resourceVersions of everything the apply reads.

        The document is stable across a rotation now, so without this nothing
        would re-run the import and Keycloak would keep the old credential
        while every consumer had already switched.
        """
        digest = hashlib.sha256()
        for name in sorted(set(names)):
            digest.update(f"{name}={version_of(self.secret(name))}\0".encode())
        return "sha256:" + digest.hexdigest()

    def base_realm(self) -> dict:
        raw = parse_realm_document(self._base_realm_text())
        return raw.get("realm_representation", raw)

    def _base_realm_text(self) -> str:
        """The base realm document, read by name from the API every reconcile.

        By name, and not from any copy on disk. A projected volume is
        refreshed lazily, so on a notification it still holds the previous
        bytes. A sidecar-delivered copy would be worse: the sidecar selects by
        *label*, so any labelled ConfigMap in the namespace could become the
        realm -- and unlike a fragment, the base realm is applied wholesale,
        with none of the composer's rails on what it may contain. The watch is
        only the doorbell; this is the read.

        The path is what the CLI uses, where there is no cluster to ask.
        """
        name = self.settings.base_realm_configmap
        if not name:
            return Path(self.settings.base_realm_path).read_text(encoding="utf-8")
        configmap = self.client.get_configmap(self.namespace, name) or {}
        text = (configmap.get("data") or {}).get(self.settings.base_realm_key)
        if not text:
            raise FileNotFoundError(
                f"configmap {self.namespace}/{name} has no "
                f"{self.settings.base_realm_key}"
            )
        return text

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
        self._secrets.clear()
        prior = prior_installed(self.state)
        loaded = scan(self.settings.fragment_dir)
        base = self.base_realm()
        resolvable = self.client_secret_keys()
        result = compose(
            base,
            loaded,
            self.site(),
            self.resolve_secret,
            reserved_env=frozenset(resolvable),
        )

        reasons = {str(item.ref): why for item, why in result.rejected}
        statuses = [
            self._status(item, reasons.get(str(item.ref)), prior) for item in loaded
        ]
        held = self._retracting(prior, {s.ref for s in statuses})
        statuses.extend(held)

        self._log_decisions(statuses)
        document = canonical(result.realm)
        desired_hash = document_hash(document)
        base_hash = realm_hash(base)
        bound = {b.name for b in result.bindings.values()}
        secrets_version = self.secrets_version(
            [self.settings.client_secrets_secret] + sorted(bound)
        )
        self._watched_secrets = {
            self.settings.client_secrets_secret,
            self.settings.admin_secret,
        } | referenced_secrets(loaded)
        # Everything config-cli will have in its environment: the base realm's
        # Secret taken wholesale, one variable per fragment client, and the
        # site hostname every base-realm URL is written against.
        available = resolvable | set(result.bindings) | {substitution.SERVER_HOSTNAME}
        missing = substitution.unresolved(document, available)

        self.state.fragments = statuses
        self.state.last_reconcile = now()
        self.state.base_hash = base_hash
        self.state.composed_hash = desired_hash
        self.state.secrets_version = secrets_version
        self.state.identical_to_base = desired_hash == base_hash
        self.state.live_checksum = self.keycloak.import_checksum()
        self.state.drift = self._drifted()
        self.state.pending_change = (
            desired_hash != self.state.last_applied_hash
            or secrets_version != self.state.applied_secrets_version
            or self.state.drift
        )

        if held:
            return self._hold(held)
        if missing:
            return self._refuse_unresolved(missing)
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
        self._apply(result, document, desired_hash, secrets_version)
        self.store.save(self.state)
        return self.state

    def _drifted(self) -> bool:
        """Did something other than this reconciler write the realm?

        Only answerable between two known values. An unreadable realm, or an
        apply whose read-back did not come through, leaves no expectation to
        compare against -- and "we do not know" must not become "re-apply".
        """
        expected = self.state.applied_import_checksum
        live = self.state.live_checksum
        if not expected or not live or expected == live:
            return False
        log.warning(
            "DRIFT     the realm was last written by something other than this "
            "reconciler (import checksum %s, expected %s); re-applying",
            live[:12],
            expected[:12],
        )
        return True

    def _refuse_unresolved(self, missing: list[str]) -> State:
        """A named credential with nothing behind it is not a partial apply.

        config-cli leaves an unresolved `$(env:x)` alone and Keycloak stores
        that string as the client's secret -- a working-looking client anyone
        who can read the realm can authenticate as. There is no per-client way
        out of it either: a base-realm client cannot be dropped the way a
        fragment can, so the whole apply stops here.
        """
        self.state.phase = REFUSED
        self.state.last_result = (
            "refusing to apply: the realm names "
            + ", ".join(f"$(env:{name})" for name in missing)
            + f", which {self.settings.client_secrets_secret} does not resolve"
        )
        log.error("REFUSED   %s", self.state.last_result)
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

    def _apply(
        self,
        result: ComposeResult,
        document: str,
        desired_hash: str,
        secrets_version: str,
    ) -> None:
        self.applier.publish(document)
        # The Job name keys off both, so a rotation with an unchanged document
        # is a distinct attempt rather than a reused name.
        ok, detail = self.applier.run(
            document_hash(desired_hash + secrets_version), result.bindings
        )
        if not ok:
            log.error("APPLY     FAILED for realm %s: %s", desired_hash[:19], detail)
            self.state.phase = FAILED
            self.state.last_result = f"apply failed: {detail}"
            return
        log.info("APPLY     succeeded; realm is now %s", desired_hash[:19])
        stamp = now()
        # What config-cli actually recorded, which is the only thing a later
        # drift check can compare against. Read after the apply rather than
        # computed: the checksum covers the post-substitution document plus a
        # salt, neither of which this process should have to reproduce.
        self.state.applied_import_checksum = self.keycloak.import_checksum()
        self.state.live_checksum = self.state.applied_import_checksum
        self.state.drift = False
        self.state.last_applied_hash = desired_hash
        self.state.applied_secrets_version = secrets_version
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
