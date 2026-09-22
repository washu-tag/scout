"""The properties the design claims, one class each.

Several are claims about writes that must *not* happen, which is why the fakes
keep a write log rather than only modelling state.
"""

from __future__ import annotations

import base64
import logging

import pytest
from conftest import (
    ApiError,
    FakeKeycloak,
    TIERS,
    TransportError,
    fragment_text,
    translate,
)

from scout_keycloak_fragment_reconciler import core
from scout_keycloak_fragment_reconciler.core import APPLIED, REJECTED, Reconciler


class TestConvergence:
    """A second pass over unchanged inputs issues zero writes."""

    def test_second_pass_writes_nothing(self, reconciler, kc):
        reconciler.reconcile_once()
        assert kc.writes, "the first pass should have created something"
        after_first = list(kc.writes)

        reconciler.reconcile_once()
        assert kc.writes == after_first, "a second pass wrote: " + ", ".join(
            kc.writes[len(after_first) :]
        )

    def test_first_pass_writes_exactly_the_expected_objects(self, reconciler, kc):
        reconciler.reconcile_once()
        assert kc.writes == [
            "create_client:hello",
            "create_role:hello-admin",
            "create_role:hello-user",
            "create_mapper:client-roles",
            "add_edge:scout-user->hello-user",
            "add_edge:scout-admin->hello-admin",
        ]

    def test_reordered_redirect_uris_are_not_drift(self, reconciler, kc):
        """Keycloak does not promise list order back; treating it as drift
        would make every pass a write."""
        reconciler.reconcile_once()
        uuid = next(iter(kc.clients))
        kc.clients[uuid]["redirectUris"] = list(
            reversed(kc.clients[uuid]["redirectUris"])
        )
        kc.clients[uuid]["defaultClientScopes"] = list(
            reversed(kc.clients[uuid]["defaultClientScopes"])
        )
        before = list(kc.writes)
        reconciler.reconcile_once()
        assert kc.writes == before


class TestNonAdoption:
    """Never mutate a Keycloak object lacking our stamp.

    What makes disjointness from the base realm structural rather than
    conventional.
    """

    def test_unstamped_client_is_never_touched(
        self, reconciler, kc, k8s, unowned_client
    ):
        kc.seed_client(unowned_client)
        before = dict(kc.clients)

        reconciler.reconcile_once()

        assert kc.writes == []
        assert kc.clients == before
        assert "FragmentRejected" in k8s.reasons()

    def test_rejection_names_the_reason(self, reconciler, kc, unowned_client):
        kc.seed_client(unowned_client)
        reconciler.reconcile_once()
        outcome = next(o for o in reconciler.snapshot.outcomes)
        assert outcome.status == REJECTED
        assert "does not carry" in outcome.detail

    def test_gc_ignores_unstamped_clients(self, reconciler, kc, k8s, unowned_client):
        """An orphan is a *stamped* client with no fragment. A base-realm
        client has no fragment either, and must never be collected."""
        kc.seed_client(unowned_client)
        k8s.remove_fragment()
        reconciler.reconcile_once()
        reconciler.collect(reconciler.snapshot, now=1e9)
        assert kc.writes == []
        assert len(kc.clients) == 1


class TestBoundedBlastRadius:
    """Only stamped clients, their roles, mappers, and tier edges."""

    def test_writes_touch_nothing_else(self, reconciler, kc):
        reconciler.reconcile_once()
        kinds = {w.split(":")[0] for w in kc.writes}
        assert kinds <= {
            "create_client",
            "update_client",
            "delete_client",
            "create_role",
            "delete_role",
            "create_mapper",
            "update_mapper",
            "delete_mapper",
            "add_edge",
            "remove_edge",
        }

    def test_the_admin_surface_has_no_group_or_user_verb(self):
        """Structural: there is no method here to touch a group or a user, so
        no bug in `core` can reach one. Users and groups also stay 403 under
        the measured RBAC, so this is belt and braces."""
        from scout_keycloak_fragment_reconciler.keycloak import Admin

        surface = {name for name in dir(Admin) if not name.startswith("_")}
        assert not [n for n in surface if "group" in n or "user" in n]

    def test_a_hostile_name_cannot_walk_out_of_its_collection(self):
        """Structural, and the second lock behind the fragment contract.

        The default `quote` leaves `/` alone and httpx resolves `..` before the
        request goes out, so an unencoded name in a path could turn a delete
        aimed at a fragment's own client role into one aimed at a base realm
        tier role -- the one thing ADR 0037 says this service can never do.
        """
        import httpx2 as httpx

        from scout_keycloak_fragment_reconciler.keycloak import _seg

        hostile = "../../../roles/scout-admin"
        url = httpx.URL(
            f"https://kc/admin/realms/scout/clients/UUID/roles/{_seg(hostile)}"
        )
        assert url.raw_path.decode().startswith(
            "/admin/realms/scout/clients/UUID/roles/"
        )

    def test_tier_edges_only_name_our_own_roles(self, reconciler, kc):
        reconciler.reconcile_once()
        uuid = next(iter(kc.clients))
        ours = {r["name"] for r in kc.client_roles(uuid)}
        for tier in TIERS:
            assert kc.edges(tier) <= ours


class TestInvalidityDoesNotRemove:
    """Absence removes; invalidity does not.

    Otherwise a typo becomes an outage for a running app.
    """

    def test_invalid_edit_leaves_the_working_client(self, reconciler, kc, k8s):
        reconciler.reconcile_once()
        uuid = next(iter(kc.clients))
        before = dict(kc.clients[uuid])

        k8s.add_fragment(fragment_text().replace("displayName:", "displayNme:"))
        reconciler.reconcile_once()

        assert kc.clients[uuid] == before
        assert "FragmentInvalid" in k8s.reasons()

    def test_invalid_edit_does_not_start_a_grace_clock(self, reconciler, k8s):
        reconciler.reconcile_once()
        k8s.add_fragment("this: is not a fragment at all")
        reconciler.reconcile_once()
        assert (
            reconciler.first_absent_at == {}
        ), "a present-but-invalid fragment was treated as an orphan"

    def test_unreadable_secret_does_not_remove_the_client(self, reconciler, kc, k8s):
        reconciler.reconcile_once()
        uuid = next(iter(kc.clients))
        k8s.secrets.clear()
        reconciler.reconcile_once()
        assert uuid in kc.clients
        assert "FragmentFailed" in k8s.reasons()

    def test_a_named_but_invalid_client_survives_the_grace_period(
        self, reconciler, kc, k8s
    ):
        """The document parses, so we know which client it is about, and it is
        protected however long it stays broken."""
        reconciler.reconcile_once()
        k8s.add_fragment(
            fragment_text().replace(
                "https://hello.scout.example.edu/auth/callback",
                "https://evil.example.com/steal",
            )
        )

        snapshot = reconciler.take_snapshot()
        reconciler.collect(snapshot, now=1000.0)
        reconciler.collect(snapshot, now=1000.0 + 10_000)

        assert kc.find_client("hello") is not None
        assert reconciler.first_absent_at == {}

    def test_a_wholly_unparsable_fragment_still_protects_its_client(
        self, reconciler, kc, k8s
    ):
        """No clientId to key on, so the only signal left is that the ConfigMap
        which produced this client is still there and still broken."""
        reconciler.reconcile_once()
        k8s.add_fragment("{{{ not yaml at all")

        snapshot = reconciler.take_snapshot()
        reconciler.collect(snapshot, now=1000.0)
        reconciler.collect(snapshot, now=1000.0 + 10_000)

        assert kc.find_client("hello") is not None

    def test_a_configmap_emptied_of_data_keeps_its_client(self, reconciler, kc, k8s):
        """A rendering that produced no `data` says nothing about which client
        the ConfigMap was about -- the same epistemic state as a document that
        does not parse, so the client is kept until the fragment is fixed."""
        reconciler.reconcile_once()
        item = k8s.get_configmap("demo", "hello-keycloak")
        item["data"] = {}
        k8s.configmaps = [item]

        snapshot = reconciler.take_snapshot()
        for now in (0.0, reconciler.settings.orphan_grace_seconds + 1):
            reconciler.collect(snapshot, now=now)

        assert kc.find_client("hello") is not None
        assert not [w for w in kc.writes if w.startswith("delete_client")]

    def test_a_fragment_shipped_under_binarydata_keeps_its_client(
        self, reconciler, kc, k8s
    ):
        """The same state reached the other way: the document is there, under a
        key this service does not read, so it declares nothing it can see."""
        reconciler.reconcile_once()
        item = k8s.get_configmap("demo", "hello-keycloak")
        item["binaryData"] = {
            "fragment.yaml": base64.b64encode(fragment_text().encode()).decode()
        }
        del item["data"]
        k8s.configmaps = [item]

        snapshot = reconciler.take_snapshot()
        for now in (0.0, reconciler.settings.orphan_grace_seconds + 1):
            reconciler.collect(snapshot, now=now)

        assert kc.find_client("hello") is not None

    def test_dropping_one_client_from_a_valid_fragment_does_collect_it(
        self, reconciler, kc, k8s
    ):
        """The other side of the coin: once the fragment parses again and
        simply no longer declares the client, the author's intent is legible
        and the client goes."""
        two = fragment_text("hello") + fragment_text("second").split("clients:")[1]
        k8s.add_fragment(two)
        k8s.add_secret("second-secret", name="second-keycloak-client")
        reconciler.reconcile_once()
        assert {c["clientId"] for c in kc.clients.values()} == {"hello", "second"}

        k8s.add_fragment(fragment_text("hello"))
        snapshot = reconciler.take_snapshot()
        reconciler.collect(snapshot, now=1000.0)
        reconciler.collect(snapshot, now=1000.0 + 301)

        assert {c["clientId"] for c in kc.clients.values()} == {"hello"}


class TestIsolation:
    """An invalid fragment produces no writes and blocks no other fragment."""

    def test_a_broken_fragment_does_not_block_a_good_one(self, reconciler, kc, k8s):
        k8s.add_fragment("nonsense: true", namespace="other", name="broken")
        k8s.add_fragment(fragment_text("second"), namespace="other", name="second")
        k8s.add_secret(
            "second-secret", namespace="other", name="second-keycloak-client"
        )

        reconciler.reconcile_once()

        assert {c["clientId"] for c in kc.clients.values()} == {"hello", "second"}

    def test_two_documents_in_one_configmap_fail_independently(
        self, reconciler, kc, k8s
    ):
        item = k8s.get_configmap("demo", "hello-keycloak")
        item["data"]["broken.yaml"] = "apiVersion: nope/v1\nkind: KeycloakFragment"
        k8s.configmaps = [item]

        reconciler.reconcile_once()

        assert "hello" in {c["clientId"] for c in kc.clients.values()}
        assert "FragmentInvalid" in k8s.reasons()

    def test_a_broken_sibling_document_protects_the_other_client(
        self, reconciler, kc, k8s
    ):
        """A typo in one document must not collect the client another document
        in the same ConfigMap declared. The broken document cannot say which
        clientId it was about, so any unclaimed client from that source might
        be the one it meant -- one sibling parsing says nothing about it."""
        item = k8s.get_configmap("demo", "hello-keycloak")
        item["data"]["second.yaml"] = fragment_text("second")
        k8s.configmaps = [item]
        k8s.add_secret("second-secret", name="second-keycloak-client")
        reconciler.reconcile_once()
        assert {c["clientId"] for c in kc.clients.values()} == {"hello", "second"}

        item["data"]["second.yaml"] = "{{{ not yaml at all"
        for now in (0.0, reconciler.settings.orphan_grace_seconds + 1):
            snapshot = reconciler.take_snapshot()
            reconciler.collect(snapshot, now=now)

        assert kc.find_client("second") is not None
        assert not [w for w in kc.writes if w.startswith("delete_client")]

    def test_a_repaired_sibling_document_releases_the_protection(
        self, reconciler, kc, k8s
    ):
        """The protection is presence-of-a-broken-document, not permanence:
        once every document parses and none declares the client, it collects.
        This is how removing one client out of several works."""
        item = k8s.get_configmap("demo", "hello-keycloak")
        item["data"]["second.yaml"] = fragment_text("second")
        k8s.configmaps = [item]
        k8s.add_secret("second-secret", name="second-keycloak-client")
        reconciler.reconcile_once()

        del item["data"]["second.yaml"]
        for now in (0.0, reconciler.settings.orphan_grace_seconds + 1):
            snapshot = reconciler.take_snapshot()
            reconciler.collect(snapshot, now=now)

        assert kc.find_client("second") is None
        assert kc.find_client("hello") is not None

    def test_an_unknown_apiversion_is_skipped_whole(self, reconciler, kc, k8s):
        k8s.add_fragment(
            fragment_text().replace("v1alpha1", "v99"),
        )
        reconciler.reconcile_once()
        assert kc.clients == {}
        message = next(m for m in (e["message"] for e in k8s.events))
        assert "apiVersion" in message and "skipping" in message


class TestPreconditions:
    """The tier roles must pre-exist. Absent is retryable, never repaired."""

    def test_absent_tier_roles_block_the_pass(self, settings, k8s):
        kc = FakeKeycloak(tier_roles=[])
        reconciler = Reconciler(settings, k8s, kc)

        reconciler.reconcile_once()

        assert kc.writes == []
        assert kc.realm_roles == {}, "the reconciler created a tier role"
        assert not reconciler.tiers_present

    def test_a_partial_tier_set_also_blocks(self, settings, k8s):
        kc = FakeKeycloak(tier_roles=["scout-user"])
        reconciler = Reconciler(settings, k8s, kc)
        reconciler.reconcile_once()
        assert kc.writes == []
        assert not reconciler.tiers_present

    def test_it_recovers_once_the_realm_is_applied(self, settings, k8s):
        kc = FakeKeycloak(tier_roles=[])
        reconciler = Reconciler(settings, k8s, kc)
        reconciler.reconcile_once()

        for name in TIERS:
            kc.realm_roles[name] = {"id": f"realm-{name}", "name": name}
            kc.composites[name] = {}
        reconciler.reconcile_once()

        assert reconciler.tiers_present
        assert "create_client:hello" in kc.writes

    def test_an_unreadable_realm_is_retryable_not_fatal(self, settings, k8s, kc):
        from scout_keycloak_fragment_reconciler.keycloak import KeycloakError

        kc.fail_on["realm_role"] = KeycloakError(503, "keycloak restarting")
        reconciler = Reconciler(settings, k8s, kc)
        reconciler.reconcile_once()
        assert not reconciler.tiers_present
        assert kc.writes == []


class TestSafePartialState:
    """Any interruption leaves something inert, never over-permissive.

    Hence `fullScopeAllowed: false` at create time rather than patched in after.
    """

    def test_full_scope_is_false_on_the_create_call(self, reconciler, kc):
        reconciler.reconcile_once()
        client = kc.find_client("hello")
        assert client["fullScopeAllowed"] is False

    def test_every_prefix_of_the_apply_is_inert(self, reconciler, kc):
        """A client with no roles yields an empty claim and the app 403s. Walk
        the write order and assert no prefix grants anything."""
        reconciler.reconcile_once()
        order = [w for w in kc.writes]
        first_edge = next(i for i, w in enumerate(order) if w.startswith("add_edge"))
        # Nothing before the first edge can make a role reach a user.
        assert all(not w.startswith("add_edge") for w in order[:first_edge])
        # And the client itself was created before any role existed.
        assert order[0].startswith("create_client")

    def test_a_failure_midway_leaves_no_edges(self, settings, k8s, kc):
        from scout_keycloak_fragment_reconciler.keycloak import KeycloakError

        kc.fail_on["create_protocol_mapper"] = KeycloakError(500, "boom")
        reconciler = Reconciler(settings, k8s, kc)
        reconciler.reconcile_once()

        assert kc.find_client("hello") is not None
        for tier in TIERS:
            assert kc.edges(tier) == set(), "an edge survived a failed apply"


class TestOneWaySecretFlow:
    """Secret to Keycloak only. Never generated, never written back."""

    def test_the_client_secret_comes_from_the_kubernetes_secret(self, reconciler, kc):
        reconciler.reconcile_once()
        assert kc.secrets[next(iter(kc.clients))] == "hello-secret"

    def test_the_k8s_adapter_cannot_write_a_secret(self):
        from scout_keycloak_fragment_reconciler.k8s import Client

        surface = {n for n in dir(Client) if not n.startswith("_")}
        writers = {n for n in surface if "secret" in n} - {"get_secret"}
        assert not writers, f"a Secret write crept in: {writers}"

    def test_a_rotation_is_pushed_to_keycloak_not_pulled_from_it(
        self, reconciler, kc, k8s
    ):
        reconciler.reconcile_once()
        uuid = next(iter(kc.clients))
        k8s.add_secret("rotated")

        reconciler.reconcile_once()

        assert kc.secrets[uuid] == "rotated"
        assert "update_client:hello" in kc.writes


class TestGarbageCollection:
    """The GC rail: absence removes, but only from a read that completed."""

    def test_a_failed_list_never_collects(self, reconciler, kc, k8s):
        reconciler.reconcile_once()
        before = dict(kc.clients)

        k8s.list_error = TransportError("connection refused")
        reconciler.reconcile_once()

        assert kc.clients == before
        assert reconciler.first_absent_at == {}

    def test_an_empty_completed_list_does_collect_after_grace(
        self, reconciler, kc, k8s
    ):
        reconciler.reconcile_once()
        k8s.remove_fragment()

        snapshot = reconciler.take_snapshot()
        reconciler.collect(snapshot, now=1000.0)
        assert kc.find_client("hello") is not None, "deleted inside the grace period"

        reconciler.collect(snapshot, now=1000.0 + 301)
        assert kc.find_client("hello") is None

    def test_a_fragment_that_comes_back_clears_the_clock(self, reconciler, kc, k8s):
        reconciler.reconcile_once()
        k8s.remove_fragment()
        reconciler.collect(reconciler.take_snapshot(), now=1000.0)
        assert "hello" in reconciler.first_absent_at

        k8s.add_fragment(fragment_text())
        k8s.add_secret("hello-secret")
        reconciler.reconcile_once()

        assert reconciler.first_absent_at == {}
        assert kc.find_client("hello") is not None

    def test_a_rename_is_an_update_not_a_delete_and_recreate(self, reconciler, kc, k8s):
        """Ownership keys on the stamp alone; GC keys on the clientId being
        unclaimed. So a fragment moving to a new ConfigMap name never opens a
        window in which the client is absent."""
        reconciler.reconcile_once()
        uuid = next(iter(kc.clients))

        k8s.remove_fragment()
        k8s.add_fragment(fragment_text(), name="hello-keycloak-v2")
        reconciler.reconcile_once()

        assert "delete_client:hello" not in kc.writes
        assert uuid in kc.clients
        assert translate.source_of(kc.clients[uuid]) == "demo/hello-keycloak-v2"

    def test_a_witnessed_deletion_schedules_its_own_recheck(self, reconciler):
        assert reconciler.next_deadline() is None
        reconciler.witness_deletion("demo", "hello-keycloak")
        deadline = reconciler.next_deadline()
        assert deadline is not None and 0 < deadline <= 301

    def test_resync_disabled_means_no_timer_at_all(self, settings, k8s, kc):
        """-1 must not degrade to a long timer. A periodic pass concludes a
        fragment is gone with nothing having reported it gone, which is the
        inference -1 exists to refuse, so idle has to mean parked on the
        watch."""
        settings.resync_seconds = -1
        reconciler = Reconciler(settings, k8s, kc)
        reconciler.reconcile_once()
        assert core.next_wait(reconciler) is None

        reconciler.witness_deletion("demo", "hello-keycloak")
        deadline = core.next_wait(reconciler)
        assert deadline is not None and deadline <= 301

    def test_resync_disabled_still_collects_an_edited_away_client(
        self, settings, k8s, kc, monkeypatch
    ):
        """Dropping one client from a fragment that stays put is a MODIFIED
        event, so nothing witnesses a deletion and there is no timer behind it.
        The grace period still has to come due, which means the pass that
        started the clock has to book the wake that finishes it.

        Driven through the real scheduler: each iteration is a pass, and the
        clock advances by exactly the wait `next_wait` asked for. If it ever
        returns None here, the loop would park forever.
        """
        settings.resync_seconds = -1
        item = k8s.get_configmap("demo", "hello-keycloak")
        item["data"]["second.yaml"] = fragment_text("second")
        k8s.configmaps = [item]
        k8s.add_secret("second-secret", name="second-keycloak-client")
        reconciler = Reconciler(settings, k8s, kc)
        reconciler.reconcile_once()
        assert kc.find_client("second") is not None

        clock = [0.0]
        monkeypatch.setattr(core.time, "monotonic", lambda: clock[0])
        del item["data"]["second.yaml"]

        waits = []
        for _ in range(8):
            snapshot = reconciler.take_snapshot()
            reconciler.collect(snapshot, now=clock[0])
            if kc.find_client("second") is None:
                break
            wait = core.next_wait(reconciler)
            if wait is None:
                break
            waits.append(wait)
            clock[0] += wait

        assert (
            kc.find_client("second") is None
        ), f"never collected; the loop stopped after waits {waits}"
        assert kc.find_client("hello") is not None

    def test_a_grace_period_in_progress_books_its_own_wake(self, settings, k8s, kc):
        """The mechanism behind the above, asserted directly: a pass that
        starts an orphan's grace clock must leave a deadline behind it."""
        settings.resync_seconds = -1
        reconciler = Reconciler(settings, k8s, kc)
        reconciler.reconcile_once()
        k8s.remove_fragment()

        snapshot = reconciler.take_snapshot()
        reconciler.collect(snapshot, now=0.0)

        assert "hello" in reconciler.first_absent_at
        assert reconciler.next_deadline() is not None


class TestDriftIsAnAnomaly:
    """If a resync has to re-add a tier edge, say so loudly.

    These edges are only ever written here, so one going missing means the base
    realm's tier roles gained a `composites` key and will keep reaping every
    fragment's grant.
    """

    def test_a_first_grant_is_not_an_anomaly(self, reconciler, kc, caplog):
        with caplog.at_level(logging.ERROR):
            reconciler.reconcile_once()
        assert reconciler.drift_repairs == 0
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_a_reaped_edge_is_logged_at_error_and_counted(self, reconciler, kc, caplog):
        reconciler.reconcile_once()
        # What a base-realm apply with a `composites` key would do.
        kc.composites["scout-user"].clear()

        with caplog.at_level(logging.ERROR):
            reconciler.reconcile_once()

        assert reconciler.drift_repairs == 1
        assert kc.edges("scout-user") == {"hello-user"}
        assert any("composites" in r.message for r in caplog.records)

    def test_a_deletion_waits_out_the_grace_period(self, reconciler, kc, k8s):
        reconciler.reconcile_once()
        k8s.remove_fragment()
        snapshot = reconciler.take_snapshot()
        # Two calls: the first starts the clock, the second finds it elapsed.
        reconciler.collect(snapshot, now=1000.0)
        assert kc.find_client("hello") is not None
        reconciler.collect(snapshot, now=1000.0 + 301)
        assert kc.find_client("hello") is None


class TestArbitration:
    """clientId uniqueness across fragments, which one fragment cannot answer."""

    def test_two_unowned_claims_fail_both(self, reconciler, kc, k8s):
        k8s.add_fragment(fragment_text(), namespace="other", name="rival")
        k8s.add_secret("rival-secret", namespace="other", name="hello-keycloak-client")

        reconciler.reconcile_once()

        assert kc.writes == []
        statuses = {o.status for o in reconciler.snapshot.outcomes}
        assert statuses == {REJECTED}
        assert len(reconciler.snapshot.outcomes) == 2

    def test_the_incumbent_stamp_wins(self, reconciler, kc, k8s):
        reconciler.reconcile_once()
        before = list(kc.writes)

        k8s.add_fragment(fragment_text(), namespace="other", name="rival")
        k8s.add_secret("rival-secret", namespace="other", name="hello-keycloak-client")
        reconciler.reconcile_once()

        assert kc.writes == before, "the rival was allowed to write"
        rejected = [o for o in reconciler.snapshot.outcomes if o.status == REJECTED]
        assert len(rejected) == 1
        assert rejected[0].source == "other/rival"
        assert "demo/hello-keycloak" in rejected[0].detail

    def test_one_configmap_claiming_a_clientid_twice_names_both_documents(
        self, reconciler, kc, k8s
    ):
        """A duplicate inside one artifact is the author's to fix, and the
        cross-fragment tie-break cannot resolve it: its tie-breaker is which
        source owns the client, which says nothing about which of that source's
        documents should win. So it must be reported, not silently resolved by
        sort order -- and each rejection needs its own document label, or the
        two outcomes collide as one metric series."""
        item = k8s.get_configmap("demo", "hello-keycloak")
        item["data"]["dupe.yaml"] = fragment_text()
        k8s.configmaps = [item]

        reconciler.reconcile_once()

        assert kc.writes == []
        rejected = [o for o in reconciler.snapshot.outcomes if o.status == REJECTED]
        assert len(rejected) == 2
        assert {o.document for o in rejected} == {"dupe.yaml", "fragment.yaml"}
        for outcome in rejected:
            others = {"dupe.yaml", "fragment.yaml"} - {outcome.document}
            assert others.pop() in outcome.detail

    def test_an_owners_own_duplicate_does_not_hand_its_client_to_a_rival(
        self, reconciler, kc, k8s
    ):
        """Incumbency is settled before any claim is dropped. Otherwise a
        mistake inside the owning artifact -- a chart rendering the fragment
        twice -- leaves a rival as the only claimant standing, and the rival's
        representation is written over the owner's client, credential and all.
        """
        reconciler.reconcile_once()
        k8s.add_fragment(fragment_text(), namespace="other", name="rival")
        k8s.add_secret("rival-secret", namespace="other", name="hello-keycloak-client")
        reconciler.reconcile_once()

        item = k8s.get_configmap("demo", "hello-keycloak")
        item["data"]["dupe.yaml"] = fragment_text()
        reconciler.reconcile_once()

        live = kc.find_client("hello")
        assert translate.source_of(live) == "demo/hello-keycloak"
        assert kc.client_secret(live["id"]) == "hello-secret"
        assert "update_client:hello" not in kc.writes

    def test_a_duplicate_does_not_block_another_fragments_claim(
        self, reconciler, kc, k8s
    ):
        """The disqualified source is out of contention, so a well-formed rival
        is then the only claimant and applies normally."""
        item = k8s.get_configmap("demo", "hello-keycloak")
        item["data"]["dupe.yaml"] = fragment_text()
        k8s.configmaps = [item]
        k8s.add_fragment(fragment_text(), namespace="other", name="rival")
        k8s.add_secret("rival-secret", namespace="other", name="hello-keycloak-client")

        reconciler.reconcile_once()

        applied = [o for o in reconciler.snapshot.outcomes if o.status == APPLIED]
        assert [o.source for o in applied] == ["other/rival"]


class TestDryRun:
    def test_dry_run_performs_no_writes(self, settings, k8s, kc):
        settings.dry_run = True
        reconciler = Reconciler(settings, k8s, kc)
        reconciler.reconcile_once()
        assert kc.writes == []
        assert kc.clients == {}


class TestNamespaceAllowlist:
    def test_a_fragment_outside_the_allowlist_is_not_read(self, settings, k8s, kc):
        settings.watched_namespaces = ["demo"]
        k8s.add_fragment(fragment_text("elsewhere"), namespace="nope", name="x")
        reconciler = Reconciler(settings, k8s, kc)

        reconciler.reconcile_once()

        assert {c["clientId"] for c in kc.clients.values()} == {"hello"}

    def test_emptying_the_allowlist_orphans_nothing(self, settings, k8s, kc):
        """Empty is the chart's default, so this is the state a values file
        that stops setting `watchedNamespaces` falls into. Reading nothing
        must not read as everything having been deleted -- that would take out
        every fragment client in the realm on a config edit."""
        reconciler = Reconciler(settings, k8s, kc)
        reconciler.reconcile_once()
        settings.watched_namespaces = []

        for _ in range(3):
            snapshot = reconciler.take_snapshot()
            reconciler.collect(snapshot, now=1_000_000.0)

        assert kc.find_client("hello") is not None
        assert not [w for w in kc.writes if w.startswith("delete_client")]

    def test_an_unwatched_namespace_does_not_orphan_its_client(self, settings, k8s, kc):
        """Narrowing the allowlist must not read as the fragments it stops
        covering having been deleted. Once non-empty the allowlist is
        documented as a read filter, so an operator trimming it to reduce API
        reads would otherwise delete the Keycloak clients of every app outside
        the new scope."""
        reconciler = Reconciler(settings, k8s, kc)
        reconciler.reconcile_once()
        settings.watched_namespaces = ["somewhere-else"]

        # Well past any grace period: the client is not a candidate at all, so
        # no amount of waiting turns it into one.
        for _ in range(3):
            snapshot = reconciler.take_snapshot()
            reconciler.collect(snapshot, now=1_000_000.0)

        assert kc.find_client("hello") is not None
        assert "hello" not in reconciler.first_absent_at
        assert not [w for w in kc.writes if w.startswith("delete_client")]

    def test_narrowing_the_allowlist_still_collects_what_it_covers(
        self, settings, k8s, kc
    ):
        """The scope check must not disable GC for the namespaces still in it,
        or the fix for the above would trade one silent failure for another."""
        reconciler = Reconciler(settings, k8s, kc)
        reconciler.reconcile_once()
        settings.watched_namespaces = ["demo"]
        k8s.remove_fragment()

        snapshot = reconciler.take_snapshot()
        reconciler.collect(snapshot, now=0.0)
        reconciler.collect(snapshot, now=settings.orphan_grace_seconds + 1)

        assert kc.find_client("hello") is None


@pytest.mark.parametrize(
    "op",
    [
        "find_client",
        "list_clients",
        "client_roles",
        "protocol_mappers",
        "tier_edges_for_client",
    ],
)
def test_a_keycloak_read_failure_never_ends_the_pass(settings, k8s, kc, op):
    from scout_keycloak_fragment_reconciler.keycloak import KeycloakError

    kc.fail_on[op] = KeycloakError(500, "boom")
    reconciler = Reconciler(settings, k8s, kc)
    # No exception escapes: a bad pass must not end the loop, because the
    # clients already in the realm keep working and the next pass is a fresh
    # read.
    reconciler.reconcile_once()


def test_a_kubernetes_list_failure_is_not_an_empty_realm(settings, kc):
    from conftest import FakeK8s

    k8s = FakeK8s()
    k8s.list_error = ApiError(403, "forbidden")
    reconciler = Reconciler(settings, k8s, kc)
    snapshot = reconciler.take_snapshot()
    assert snapshot.complete is False
    reconciler.collect(snapshot, now=1e9)
    assert kc.writes == []


class TestEventsMarkTransitions:
    """Events report changes, not heartbeats.

    Each emission is its own object rather than an aggregated repeat, so
    reporting every pass would bury a real failure under identical lines.
    """

    def test_a_steady_state_reports_once(self, reconciler, k8s):
        reconciler.reconcile_once()
        assert k8s.reasons() == ["FragmentApplied"]
        reconciler.reconcile_once()
        reconciler.reconcile_once()
        assert k8s.reasons() == ["FragmentApplied"], "reported a heartbeat"

    def test_breaking_reports_again(self, reconciler, k8s):
        reconciler.reconcile_once()
        k8s.add_fragment("this: is not a fragment")
        reconciler.reconcile_once()
        assert k8s.reasons() == ["FragmentApplied", "FragmentInvalid"]

    def test_staying_broken_does_not_repeat(self, reconciler, k8s):
        reconciler.reconcile_once()
        k8s.add_fragment("this: is not a fragment")
        reconciler.reconcile_once()
        before = list(k8s.reasons())
        reconciler.reconcile_once()
        assert k8s.reasons() == before

    def test_recovering_reports_again(self, reconciler, k8s):
        reconciler.reconcile_once()
        k8s.add_fragment("this: is not a fragment")
        reconciler.reconcile_once()
        k8s.add_fragment(fragment_text())
        reconciler.reconcile_once()
        assert k8s.reasons() == [
            "FragmentApplied",
            "FragmentInvalid",
            "FragmentApplied",
        ]

    def test_a_changed_message_reports_again(self, reconciler, k8s):
        """Same status, different reason -- an author fixing one problem and
        hitting the next should see the new one."""
        k8s.add_fragment("apiVersion: nope/v1\nkind: KeycloakFragment")
        reconciler.reconcile_once()
        k8s.add_fragment("also: not a fragment")
        reconciler.reconcile_once()
        assert k8s.reasons() == ["FragmentInvalid", "FragmentInvalid"]
        assert len({e["message"] for e in k8s.events}) == 2
