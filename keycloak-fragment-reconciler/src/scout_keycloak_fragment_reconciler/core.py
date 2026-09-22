"""The reconcile loop: snapshot, arbitrate, apply, collect.

Every pass starts from its own authoritative LIST; the watch only ever rings a
bell, and may name a deletion it witnessed. So what distinguishes the triggers
is not what a pass may conclude but what prompted it: a periodic pass can find
drift nothing reported, and can conclude a fragment is gone without anything
having said so. `resync_seconds: -1` is an operator declining that second
power, and it removes the timer rather than lengthening it.

Apply order per fragment is chosen so every prefix is inert: a client with no
roles yields an empty claim and the app 403s. Tier edges go last, because an
edge is what makes a role reach a user.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from . import shutdown, translate
from .fragment import ClientSpec, Fragment, FragmentError, check_site_rules, parse
from .k8s import ApiError, Client, value_of
from .keycloak import Admin, KeycloakError
from .settings import Settings

log = logging.getLogger("keycloak-fragment-reconciler")

VALID = "valid"
INVALID = "invalid"
REJECTED = "rejected"
APPLIED = "applied"
FAILED = "failed"


@dataclass
class Claim:
    """One client a fragment asks for, and where it came from."""

    namespace: str
    configmap: str
    spec: ClientSpec
    # The ConfigMap `data` key it came from, so two documents in one ConfigMap
    # are distinguishable in an outcome and in a metric label set.
    document: str = ""

    @property
    def source(self) -> str:
        return f"{self.namespace}/{self.configmap}"


@dataclass
class Outcome:
    source: str
    # Empty when the document failed to parse: there is no clientId to name,
    # and this is a metric label, so it stays truthful.
    client_id: str
    status: str
    detail: str = ""
    # The ConfigMap `data` key. The only identifier a parse failure has.
    document: str = ""

    @property
    def subject(self) -> str:
        return self.client_id or self.document or "(document)"


@dataclass
class Snapshot:
    """What one pass believes about the world. Replaced whole by a LIST.

    `complete` is load-bearing: GC may only act when it is True, because
    otherwise an empty `claims` means "we could not ask" rather than "nothing
    is there".
    """

    claims: dict[str, Claim] = field(default_factory=dict)
    objects: dict[str, dict] = field(default_factory=dict)
    outcomes: list[Outcome] = field(default_factory=list)
    complete: bool = False
    # Every clientId named by a document that parsed, whether or not it then
    # passed validation. A fragment edited into invalidity still says which
    # client it is about, and that client must not be collected.
    seen_client_ids: set[str] = field(default_factory=set)
    # Sources with at least one document that did not parse. Such a document
    # cannot say which clientId it was about, so any unclaimed client from that
    # source might be the one it meant. Tracked per source rather than per
    # document because the association is exactly what was lost.
    unparsable_sources: set[str] = field(default_factory=set)


class Reconciler:
    def __init__(self, settings: Settings, k8s: Client, admin: Admin) -> None:
        self.settings = settings
        self.k8s = k8s
        self.admin = admin
        self.snapshot = Snapshot()
        # clientId -> when it was first seen absent. In memory only, so a
        # restart restarts the clock: the grace period is a floor, not a
        # guarantee, and a restart only ever delays a deletion.
        self.first_absent_at: dict[str, float] = {}
        self.writes = 0
        self.deletions = 0
        self.drift_repairs = 0
        self.tiers_present = False
        self.last_list_ok = 0.0
        self._pending_recheck: dict[str, float] = {}
        # The last (status, detail) reported per outcome, so an Event marks a
        # transition rather than repeating every pass.
        self._reported: dict[tuple[str, str, str], tuple[str, str]] = {}

    # --- preconditions --------------------------------------------------

    def check_tiers(self) -> bool:
        """The tier roles must pre-exist, and are never created here.

        Also what `/readyz` reports: without them nothing this service creates
        would reach a user, so reporting ready would be a lie.
        """
        try:
            missing = [
                name
                for name in self.settings.tier_roles
                if self.admin.realm_role(name) is None
            ]
        except KeycloakError as exc:
            log.warning("could not read the tier roles: %s", exc)
            self.tiers_present = False
            return False
        if missing:
            log.warning(
                "tier role(s) %s are absent from realm %s; the base realm has "
                "not been applied. Waiting -- these are preconditions and are "
                "never created here",
                ", ".join(missing),
                self.settings.realm,
            )
        self.tiers_present = not missing
        return self.tiers_present

    # --- discovery ------------------------------------------------------

    def take_snapshot(self) -> Snapshot:
        """An authoritative LIST, parsed and arbitrated."""
        snapshot = Snapshot()
        # Watching nothing is configured, not broken: the chart grants no
        # cluster read in that case, so asking would only be a 403 every pass.
        # Left incomplete, which is what stops GC from reading the absence of
        # fragments as their deletion.
        if not self.settings.watched_namespaces:
            return snapshot
        try:
            items = self.k8s.list_configmaps(self.settings.label_selector)
        except ApiError as exc:
            log.warning("could not list fragments (%s); GC skipped this cycle", exc)
            return snapshot
        snapshot.complete = True
        self.last_list_ok = time.time()

        contenders: dict[str, list[Claim]] = {}
        for item in items:
            meta = item.get("metadata") or {}
            namespace, name = meta.get("namespace", ""), meta.get("name", "")
            if not self.settings.watches(namespace):
                continue
            snapshot.objects[f"{namespace}/{name}"] = item
            for claim in self._read(item, snapshot):
                contenders.setdefault(claim.spec.client_id, []).append(claim)

        self._arbitrate(contenders, snapshot)
        return snapshot

    def _read(self, item: dict, snapshot: Snapshot) -> list[Claim]:
        """Every client one fragment ConfigMap asks for.

        Each `data` key is its own document and each fails alone. Every clientId
        that was named at all is recorded too, which is how GC tells a fragment
        that was emptied from one that is merely broken.
        """
        meta = item.get("metadata") or {}
        namespace, name = meta.get("namespace", ""), meta.get("name", "")
        source = f"{namespace}/{name}"
        claims: list[Claim] = []
        data = item.get("data") or {}
        if not data:
            # No document at all, so nothing says which clients this ConfigMap
            # was about: the same epistemic state as one that will not parse.
            snapshot.unparsable_sources.add(source)
            snapshot.outcomes.append(
                Outcome(source, "", INVALID, "the ConfigMap has no data")
            )
            return claims
        for key, text in sorted(data.items()):
            try:
                document: Fragment = parse(text)
            except FragmentError as exc:
                snapshot.unparsable_sources.add(source)
                snapshot.outcomes.append(
                    Outcome(source, "", INVALID, str(exc), document=key)
                )
                continue
            for spec in document.clients:
                # Named, therefore protected from GC even if it fails below.
                snapshot.seen_client_ids.add(spec.client_id)
                try:
                    check_site_rules(
                        spec,
                        hostname=self.settings.server_hostname,
                        tiers=self.settings.tier_roles,
                    )
                except FragmentError as exc:
                    snapshot.outcomes.append(
                        Outcome(source, spec.client_id, INVALID, str(exc), document=key)
                    )
                    continue
                if spec.app_origin != spec.app_url:
                    log.debug(
                        "%s: webOrigins for %s is the origin %s, from appUrl %s",
                        source,
                        spec.client_id,
                        spec.app_origin,
                        spec.app_url,
                    )
                claims.append(Claim(namespace, name, spec, document=key))
        return claims

    def _arbitrate(
        self, contenders: dict[str, list[Claim]], snapshot: Snapshot
    ) -> None:
        """Settle clientId uniqueness across the whole snapshot.

        Not answerable from one fragment, so this always runs over everything.

        Incumbency is settled before any claim is dropped. A source that
        already owns the client is the only one that may keep it, so its own
        mistakes disqualify only itself: dropping them first would leave a
        rival as the last claimant standing and hand it the credential.

        Tier edges need no arbitration: the composites POST is additive, so two
        fragments writing into the same tier role cannot clobber each other.
        """
        for client_id, all_claims in contenders.items():
            if len(all_claims) == 1:
                snapshot.claims[client_id] = all_claims[0]
                continue
            by_source: dict[str, list[Claim]] = {}
            for claim in all_claims:
                by_source.setdefault(claim.source, []).append(claim)

            incumbent = self._incumbent_source(client_id)
            if incumbent in by_source:
                for source, claims in by_source.items():
                    if source != incumbent:
                        self._reject_rivals(client_id, claims, incumbent, snapshot)
                own = by_source[incumbent]
                if len(own) == 1:
                    snapshot.claims[client_id] = own[0]
                else:
                    self._reject_duplicates(client_id, own, snapshot)
                continue

            eligible = []
            for claims in by_source.values():
                if len(claims) == 1:
                    eligible.append(claims[0])
                else:
                    self._reject_duplicates(client_id, claims, snapshot)
            if len(eligible) == 1:
                snapshot.claims[client_id] = eligible[0]
            elif eligible:
                self._reject_contested(client_id, eligible, snapshot)

    def _reject_duplicates(
        self, client_id: str, claims: list[Claim], snapshot: Snapshot
    ) -> None:
        """Reject every claim from a source that names one clientId twice.

        Two documents in one ConfigMap claiming the same clientId is a mistake
        inside a single artifact, and the author can see both halves of it. The
        cross-fragment tie-break cannot help: its tie-breaker is which source
        already owns the client, which says nothing about which of that
        source's own documents should win. Rejecting both names the conflict
        where it can be fixed instead of silently applying whichever sorted
        first.
        """
        source = claims[0].source
        documents = sorted(claim.document for claim in claims)
        log.error(
            "%s declares clientId %s in more than one document (%s); "
            "all of them are rejected until exactly one does",
            source,
            client_id,
            ", ".join(documents),
        )
        for claim in claims:
            others = [d for d in documents if d != claim.document]
            snapshot.outcomes.append(
                Outcome(
                    source,
                    client_id,
                    REJECTED,
                    "this ConfigMap also declares this clientId in "
                    f"{', '.join(others)}",
                    document=claim.document,
                )
            )

    def _reject_contested(
        self, client_id: str, claims: list[Claim], snapshot: Snapshot
    ) -> None:
        """Reject every claim on a clientId no source already owns.

        Picking one would be arbitrary, and the loser would retry forever
        against a client it cannot have.
        """
        sources = sorted(claim.source for claim in claims)
        log.error(
            "clientId %s is claimed by %s; all of them are rejected "
            "until exactly one claims it",
            client_id,
            ", ".join(sources),
        )
        for claim in claims:
            snapshot.outcomes.append(
                Outcome(
                    claim.source,
                    client_id,
                    REJECTED,
                    f"clientId is also claimed by "
                    f"{', '.join(s for s in sources if s != claim.source)}",
                    document=claim.document,
                )
            )

    def _reject_rivals(
        self, client_id: str, claims: list[Claim], incumbent: str, snapshot: Snapshot
    ) -> None:
        for claim in claims:
            snapshot.outcomes.append(
                Outcome(
                    claim.source,
                    client_id,
                    REJECTED,
                    f"clientId already belongs to {incumbent}",
                    document=claim.document,
                )
            )

    def _incumbent_source(self, client_id: str) -> str:
        try:
            live = self.admin.find_client(client_id)
        except KeycloakError as exc:
            log.warning("could not read client %s: %s", client_id, exc)
            return ""
        if live is None or not translate.is_ours(live):
            return ""
        return translate.source_of(live)

    # --- apply ----------------------------------------------------------

    def apply(self, claim: Claim) -> Outcome:
        """One fragment's client, in the order that keeps every prefix inert."""
        spec = claim.spec
        secret = self._read_secret(claim)
        if secret is None:
            return Outcome(
                claim.source,
                spec.client_id,
                FAILED,
                f"cannot read secretRef {spec.secret_ref.name} key "
                f"{spec.secret_ref.key} in {claim.namespace}. The app's chart "
                "must grant this reconciler's ServiceAccount a resourceNames-"
                "scoped get on that Secret",
            )
        desired = translate.client_representation(
            spec, secret=secret, source=claim.source
        )
        try:
            live = self.admin.find_client(spec.client_id)
            if live is None:
                log.info("creating client %s", spec.client_id)
                if self.settings.dry_run:
                    # Nothing downstream can be diffed against a client that
                    # does not exist, so stop here rather than invent a uuid.
                    return Outcome(
                        claim.source, spec.client_id, APPLIED, "dry-run: would create"
                    )
                self.writes += 1
                uuid = self.admin.create_client(desired)
                created = True
            else:
                if not translate.is_ours(live):
                    # Adopting a client that already exists in the realm needs
                    # a design of its own; until then it is not ours to touch.
                    return Outcome(
                        claim.source,
                        spec.client_id,
                        REJECTED,
                        "a client with this clientId already exists in the realm "
                        "and does not carry this reconciler's stamp; refusing to "
                        "modify it",
                    )
                uuid = live["id"]
                created = False
                self._update(uuid, live, desired, secret)
            self._reconcile_roles(uuid, spec)
            self._reconcile_mappers(uuid, spec)
            self._reconcile_tier_edges(uuid, spec, created=created)
        except KeycloakError as exc:
            level = log.warning if exc.retryable else log.error
            level("applying %s failed: %s", spec.client_id, exc)
            return Outcome(claim.source, spec.client_id, FAILED, str(exc))
        return Outcome(claim.source, spec.client_id, APPLIED)

    def _read_secret(self, claim: Claim) -> str | None:
        ref = claim.spec.secret_ref
        try:
            secret = self.k8s.get_secret(claim.namespace, ref.name)
        except ApiError as exc:
            log.warning("reading secret %s/%s: %s", claim.namespace, ref.name, exc)
            return None
        value = value_of(secret, ref.key)
        return value or None

    def _update(self, uuid: str, live: dict, desired: dict, secret: str) -> None:
        drift = translate.client_drift(live, desired)
        rotated = self._secret_rotated(uuid, secret)
        if not drift and not rotated:
            return
        if rotated:
            drift = [*drift, "secret"]
        log.info("updating client %s (%s)", desired["clientId"], ", ".join(drift))
        if self.settings.dry_run:
            return
        body = {
            **desired,
            "id": uuid,
            "attributes": translate.merged_attributes(live, desired),
        }
        self.writes += 1
        self.admin.update_client(uuid, body)

    def _secret_rotated(self, uuid: str, secret: str) -> bool:
        """Compare the live credential with the Secret's.

        A read rather than stored state. The Secret is not watched -- a
        `resourceNames`-scoped `get` cannot back one -- so a rotation is noticed
        on the next pass, which under `resync_seconds: -1` means the next time
        a fragment changes rather than within a bounded interval.
        """
        try:
            return self.admin.client_secret(uuid) != secret
        except KeycloakError as exc:
            log.warning("could not read back client secret for %s: %s", uuid, exc)
            return False

    def _reconcile_roles(self, uuid: str, spec: ClientSpec) -> None:
        live = {role["name"] for role in self.admin.client_roles(uuid)}
        desired = {role["name"] for role in translate.role_representations(spec)}
        for name in sorted(desired - live):
            log.info("creating role %s on %s", name, spec.client_id)
            if not self.settings.dry_run:
                self.writes += 1
                self.admin.create_client_role(uuid, {"name": name})
        for name in sorted(live - desired):
            # Safe: the client exists only because of this fragment.
            log.info("removing role %s from %s", name, spec.client_id)
            if not self.settings.dry_run:
                self.writes += 1
                self.admin.delete_client_role(uuid, name)

    def _reconcile_mappers(self, uuid: str, spec: ClientSpec) -> None:
        live = self.admin.protocol_mappers(uuid)
        write, delete = translate.mapper_drift(live, translate.protocol_mappers(spec))
        by_name = {m.get("name"): m for m in live}
        for mapper in write:
            log.info("writing mapper %s on %s", mapper["name"], spec.client_id)
            if self.settings.dry_run:
                continue
            self.writes += 1
            if mapper.get("id"):
                self.admin.update_protocol_mapper(uuid, mapper["id"], mapper)
            else:
                self.admin.create_protocol_mapper(uuid, mapper)
        for name in delete:
            log.info("removing mapper %s from %s", name, spec.client_id)
            if not self.settings.dry_run:
                self.writes += 1
                self.admin.delete_protocol_mapper(uuid, by_name[name]["id"])

    def _reconcile_tier_edges(
        self, uuid: str, spec: ClientSpec, *, created: bool
    ) -> None:
        """Last, because an edge is what makes a role reach a user.

        `created` separates the two reasons an edge can be missing. On a new
        client every edge is new, and routine. On a client that already existed,
        these edges are ours alone -- config-cli omits `composites` on the tier
        roles precisely so it never reconciles them -- so a missing one means
        something else removed it, most likely a `composites` key added to the
        base realm, which will keep reaping every fragment's grant. Hence an
        error rather than a quiet repair.

        A newly declared grant on an existing client also reads as drift here. A
        false alarm costs a log line; a missed one costs a silent 403.
        """
        roles_by_name = {r["name"]: r for r in self.admin.client_roles(uuid)}
        wanted = translate.tier_edges(spec)
        for tier in self.settings.tier_roles:
            want = set(wanted.get(tier, []))
            have = {
                edge["name"] for edge in self.admin.tier_edges_for_client(tier, uuid)
            }
            add = [roles_by_name[n] for n in sorted(want - have) if n in roles_by_name]
            drop = sorted(have - want)
            if add:
                names = ", ".join(r["name"] for r in add)
                if created:
                    log.info("granting %s -> %s", names, tier)
                else:
                    self.drift_repairs += 1
                    log.error(
                        "re-adding tier edge(s) %s -> %s on the existing client "
                        "%s; only this reconciler writes these, so either the "
                        "fragment just declared them or something removed them. "
                        "If the latter, check whether the base realm's %s role "
                        "gained a `composites` key",
                        names,
                        tier,
                        spec.client_id,
                        tier,
                    )
                if not self.settings.dry_run:
                    self.writes += 1
                    self.admin.add_tier_edges(tier, add)
            if drop:
                log.info("revoking %s -> %s", ", ".join(drop), tier)
                if not self.settings.dry_run:
                    self.writes += 1
                    self.admin.remove_tier_edges(
                        tier, [{"id": roles_by_name[n]["id"], "name": n} for n in drop]
                    )

    # --- garbage collection ---------------------------------------------

    def _is_orphan(self, client_id: str, client: dict, snapshot: Snapshot) -> bool:
        """Whether a stamped client has actually been abandoned.

        Four questions, all of which must fail:

        0. Could this pass have seen the fragment at all? Absence only means
           "removed" within the watch scope; outside it, absence means "not
           looked at". Without this, narrowing the allowlist reads as every
           out-of-scope app having been deleted.
        1. Does a valid fragment claim this clientId? Keying on the clientId
           rather than the source is what makes a fragment rename an ordinary
           update, with no window where the client is absent.
        2. Did any fragment *name* it, even one that failed validation? A typo
           is not a request for deletion.
        3. Is the producing ConfigMap still present with a document that will
           not parse? Such a document cannot say which client it was about, so
           it might be the one that declared this client -- and one sibling
           document parsing says nothing about the broken one. Once every
           document parses again and none declares the client, it becomes
           collectable, which is how removing one client out of several works.
        """
        if not self._in_watch_scope(client):
            return False
        if client_id in snapshot.claims or client_id in snapshot.seen_client_ids:
            return False
        source = translate.source_of(client)
        if (
            source
            and source in snapshot.objects
            and source in snapshot.unparsable_sources
        ):
            log.warning(
                "client %s is unclaimed, but its fragment %s is still present "
                "with a document that does not parse; keeping the client until "
                "the fragment is fixed or removed",
                client_id,
                source,
            )
            return False
        return True

    def _in_watch_scope(self, client: dict) -> bool:
        """Whether the fragment that produced a client is one we read.

        With `ALL` every namespace is in scope, so this is only ever a question
        once an operator has narrowed it -- at which point a client we cannot
        attribute to a watched namespace is one we must not judge. An absent or
        malformed source stamp fails closed for the same reason, and so does an
        empty allowlist: watching nothing must never read as every client's
        fragment having been deleted.
        """
        if self.settings.watches_all:
            return True
        namespace = translate.source_of(client).partition("/")[0]
        if not namespace or not self.settings.watches(namespace):
            log.debug(
                "client %s came from outside the watched namespaces; "
                "not a GC candidate",
                client.get("clientId", "(unknown)"),
            )
            return False
        return True

    def collect(self, snapshot: Snapshot, *, now: float) -> None:
        """Delete stamped clients that no fragment claims, after grace."""
        if not snapshot.complete:
            return
        try:
            live = self.admin.list_clients()
        except KeycloakError as exc:
            log.warning("could not list realm clients (%s); GC skipped", exc)
            return
        ours = {c["clientId"]: c for c in live if translate.is_ours(c)}
        orphans = {
            client_id: client
            for client_id, client in ours.items()
            if self._is_orphan(client_id, client, snapshot)
        }
        for client_id in list(self.first_absent_at):
            if client_id not in orphans:
                del self.first_absent_at[client_id]
        for client_id, client in orphans.items():
            since = self.first_absent_at.setdefault(client_id, now)
            waited = now - since
            remaining = self.settings.orphan_grace_seconds - waited
            if remaining > 0:
                log.info(
                    "client %s (from %s) has no fragment; deleting in %.0fs "
                    "unless it comes back",
                    client_id,
                    translate.source_of(client) or "an unknown fragment",
                    remaining,
                )
                # Book the wake that will actually delete it. The grace clock
                # starts at the first pass that saw the client absent, which is
                # necessarily later than the deletion `witness_deletion` booked
                # its re-check from, so that re-check lands just short of the
                # grace period and would otherwise be the last wake there is.
                self._schedule_recheck(client_id, remaining)
                continue
            self._delete(client)

    def _delete(self, client: dict) -> None:
        client_id = client["clientId"]
        log.warning(
            "deleting client %s (from %s): its fragment has been absent for "
            "longer than the grace period",
            client_id,
            translate.source_of(client) or "an unknown fragment",
        )
        if self.settings.dry_run:
            return
        try:
            self.admin.delete_client(client["id"])
        except KeycloakError as exc:
            log.warning("could not delete %s: %s", client_id, exc)
            return
        self.deletions += 1
        self.first_absent_at.pop(client_id, None)

    def witness_deletion(self, namespace: str, name: str) -> None:
        """A deletion the watch saw. Schedules its own re-check.

        It cannot act now, because the grace period applies here too and the
        fragment may return inside it; and it cannot wait for the next resync,
        because with `resync_seconds: -1` there is none. So it books a one-shot
        pass, which re-confirms absence from a fresh authoritative read rather
        than from the watch's word.
        """
        self._schedule_recheck(
            f"{namespace}/{name}", self.settings.orphan_grace_seconds + 1
        )
        log.info(
            "witnessed %s/%s deleted; re-checking in %ss",
            namespace,
            name,
            self.settings.orphan_grace_seconds + 1,
        )

    def _schedule_recheck(self, key: str, seconds: float) -> None:
        """Book a one-shot pass, keeping the soonest if one is already booked.

        Keyed by source for a witnessed deletion and by clientId for an orphan
        mid-grace; the two cannot collide, because a clientId may not contain
        the slash a source always has.
        """
        deadline = time.monotonic() + seconds
        existing = self._pending_recheck.get(key)
        if existing is None or deadline < existing:
            self._pending_recheck[key] = deadline

    def next_deadline(self) -> float | None:
        """Seconds until the soonest scheduled re-check, if any.

        Expired entries are dropped as they are read: each books one pass, and
        that pass either acts or books the next one from what it found.
        """
        if not self._pending_recheck:
            return None
        now = time.monotonic()
        soonest = min(self._pending_recheck.values())
        for source, deadline in list(self._pending_recheck.items()):
            if deadline <= now:
                del self._pending_recheck[source]
        return max(soonest - now, 0.0)

    # --- one pass -------------------------------------------------------

    def reconcile_once(self) -> None:
        if not self.check_tiers():
            return
        before = self.writes
        snapshot = self.take_snapshot()
        # GC before apply, so a fragment that changed clientId resolves in one
        # cycle rather than against its own stale state.
        self.collect(snapshot, now=time.time())
        for claim in snapshot.claims.values():
            snapshot.outcomes.append(self.apply(claim))
        self.snapshot = snapshot
        self._report(snapshot)
        if self.writes == before:
            log.debug("nothing to do")
        else:
            log.info("pass complete: %s write(s)", self.writes - before)

    def _report(self, snapshot: Snapshot) -> None:
        """Per-fragment outcome to Events, best effort, on change only.

        Not onto the ConfigMap: it is Flux- or Helm-managed, so a status write
        there starts a revert loop, which is why the RBAC grants no `patch`.

        On change only because each emission is a separate object rather than an
        aggregated repeat, so reporting every pass would bury the one
        interesting failure under identical "applied" lines. Steady state is
        what the metrics are for.
        """
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # Forget anything this pass produced no outcome for, or a flap back to
        # a previously-reported state stays silent: a parse failure and a
        # successful apply are keyed differently, so a stale "applied" entry
        # would survive the outage and suppress the recovery.
        live = {(o.source, o.client_id, o.document) for o in snapshot.outcomes}
        for key in list(self._reported):
            if key not in live:
                del self._reported[key]

        for outcome in snapshot.outcomes:
            involved = snapshot.objects.get(outcome.source)
            if not involved:
                continue
            key = (outcome.source, outcome.client_id, outcome.document)
            current = (outcome.status, outcome.detail)
            if self._reported.get(key) == current:
                continue
            self._reported[key] = current
            good = outcome.status == APPLIED
            # Under the same change-only gate as the Event, so a resync that
            # found nothing new stays silent.
            (log.info if good else log.warning)(
                "%s: %s is %s%s",
                outcome.source,
                outcome.subject,
                outcome.status,
                f" -- {outcome.detail}" if outcome.detail else "",
            )
            self.k8s.emit_event(
                involved=involved,
                reason={
                    APPLIED: "FragmentApplied",
                    INVALID: "FragmentInvalid",
                    REJECTED: "FragmentRejected",
                    FAILED: "FragmentFailed",
                }.get(outcome.status, "FragmentOutcome"),
                message=(
                    f"{outcome.subject}: {outcome.detail}"
                    if outcome.detail
                    else f"{outcome.subject} applied"
                ),
                event_type="Normal" if good else "Warning",
                timestamp=stamp,
            )


# --- scheduling ---------------------------------------------------------

MINIMUM_WAIT = 5.0
# How long to wait before re-testing a precondition we do not own.
TIER_RETRY_SECONDS = 30.0


def next_wait(reconciler: Reconciler) -> float | None:
    """How long to sleep when no watch event arrives, or None to sleep until one.

    The watch handles everything an app *does*; this timer exists for the three
    things no watch event will ever tell us about, and each wants a different
    interval:

    - Preconditions we do not own. `check_tiers` fails while the base realm is
      still being applied, which is the normal state during a fresh deploy. The
      whole service is idle until it passes, so retry on `TIER_RETRY_SECONDS`
      rather than at the resync interval -- but never in a tight loop, because
      the failure is usually somebody else's deploy still running. This retry
      survives `resync_seconds: -1`: it is startup, not a resync, and a pass
      that fails `check_tiers` reads nothing and concludes nothing.
    - A grace period that has to expire. `witness_deletion` books a deadline
      past the grace period so a witnessed absence is re-confirmed from a fresh
      authoritative read rather than taken on the watch's word, and `collect`
      books one for an orphan it is still waiting out. Either can legitimately
      be sooner than the resync interval, hence the `MINIMUM_WAIT` clamp: a
      grace period of 0 must not become a spin.
    - Drift nobody reported. A credential rotated in the Secret, a field
      changed in the admin console, a tier edge reaped by a realm re-apply --
      none produces a ConfigMap event, so only a periodic pass finds them. That
      pass is `resync_seconds`.

    `resync_seconds: -1` removes that third wake and returns None, parking on
    the watch indefinitely. Not a bounded-but-long wake: a periodic pass is a
    pass that concludes a fragment is gone without anything having reported it
    gone, which is the inference an operator who sets -1 is refusing. Idle here
    means genuinely idle -- nothing is re-read until the watch rings or a grace
    period comes due -- so drift goes unrepaired until something else wakes us,
    which is the trade -1 asks for. A SIGTERM sets `wake` too, so parking
    indefinitely still exits promptly.
    """
    settings = reconciler.settings
    floor = float(settings.resync_seconds) if settings.resync_seconds > 0 else None
    if not reconciler.tiers_present:
        return min(filter(None, [floor, TIER_RETRY_SECONDS]))
    deadline = reconciler.next_deadline()
    if deadline is None:
        return floor
    if floor is None:
        return max(deadline, MINIMUM_WAIT)
    return min(floor, max(deadline, MINIMUM_WAIT))


def run_forever(reconciler: Reconciler, wake: threading.Event) -> None:
    """Reconcile until asked to stop, between passes rather than during one."""
    settings = reconciler.settings
    while not shutdown.requested.is_set():
        try:
            reconciler.reconcile_once()
        except Exception:
            # Never let one bad pass end the loop: the clients already in the
            # realm keep working, and the next pass is a fresh read.
            log.exception("reconcile pass failed; the realm stays as it is")
        wait = next_wait(reconciler)
        if wait is None:
            log.debug("no periodic resync and nothing pending; waiting on the watch")
        if wake.wait(wait):
            wake.clear()
            # One watch event per object written, so let a burst land together.
            shutdown.sleep(settings.debounce_seconds)
            wake.clear()
    log.info("reconcile loop stopped; fragment clients stay as they are")
