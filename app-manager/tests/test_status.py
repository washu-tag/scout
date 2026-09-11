import hashlib
import json

import pytest
import yaml
from conftest import (  # noqa: F401
    FakeKeycloak,
    fragment_yaml,
    restart,
    setup,
    status_of,
    write_fragment,
)

from scout_app_manager.service import AppManagerService
from scout_app_manager.status import (
    HOLDING,
    INSTALLED,
    REFUSED,
    RETRACTING,
    STATUS_KEY,
    FragmentStatus,
    State,
    StatusStore,
    age_seconds,
    from_document,
    to_document,
)


def published(client) -> dict:
    data = client.configmaps[("scout-core", "scout-app-manager-status")]["data"]
    return yaml.safe_load(data[STATUS_KEY])


def test_the_document_is_camel_cased_and_carries_every_fragment(setup):
    service, fragments, client = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))

    service.reconcile_once()

    doc = published(client)
    assert doc["phase"] == "Applied"
    assert doc["baseRealmApplied"] is True
    assert doc["appliedHash"] == doc["composedHash"]
    assert doc["appliedAt"]
    entry = doc["fragments"][0]
    assert entry["ref"] == "scout-demo/hello"
    assert entry["state"] == INSTALLED
    assert entry["contentHash"].startswith("sha256:")
    assert entry["appliedAt"] == doc["appliedAt"]
    assert entry["retractingSince"] is None


def test_the_published_document_round_trips(setup):
    """`status` reads this document back, so a field that does not survive the
    trip is reported wrong -- a readable realm as unreadable, in the case that
    found this."""
    service, fragments, client = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()

    store = service.store
    restored = store.load()

    assert restored.live_checksum == client.realm_checksum
    assert restored.applied_import_checksum == client.realm_checksum
    assert restored.drift is False
    assert restored.applied_secrets_version == service.state.applied_secrets_version
    assert restored.base_source_hash == service.state.base_source_hash


def test_the_base_source_hash_is_over_the_document_a_deploy_published(setup):
    """A deploy computes this from the bytes it wrote, so it has to be exactly
    sha256 of them -- not of the parse, which is what observedBaseHash is."""
    service, fragments, client = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))

    service.reconcile_once()

    text = open(service.settings.base_realm_path, encoding="utf-8").read()
    expected = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    doc = published(client)
    assert doc["observedBaseSourceHash"] == expected
    # Two different hashes of one document, and the difference is the point.
    assert doc["observedBaseHash"] != expected


def test_the_base_source_hash_moves_when_the_document_does(setup):
    service, fragments, client = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    before = published(client)["observedBaseSourceHash"]

    path = service.settings.base_realm_path
    realm = json.loads(open(path).read())
    realm["displayName"] = "Scout, renamed"
    open(path, "w").write(json.dumps(realm))
    service.reconcile_once()

    assert published(client)["observedBaseSourceHash"] != before


def test_a_failed_apply_is_published_as_failed(setup):
    service, fragments, client = setup
    service.settings.job_timeout_seconds = 5
    client.job_succeeds = False
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))

    service.reconcile_once()

    doc = published(client)
    assert doc["phase"] == "Failed"
    assert "apply failed" in doc["lastResult"]
    assert doc["appliedHash"] is None
    assert doc["baseRealmApplied"] is False


def test_a_restart_resumes_the_applied_hash_and_does_not_reapply(setup):
    service, fragments, client = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    jobs = dict(client.jobs)

    revived = restart(service)
    revived.state.discovery_synced = True
    state = revived.reconcile_once()

    assert state.last_applied_hash is not None
    assert state.pending_change is False
    assert client.jobs == jobs


def test_a_restart_still_knows_what_it_last_wrote_to_the_realm(setup):
    """Without the checksum persisted, a restart has nothing to compare the
    live realm against and drift goes unnoticed until the next apply."""
    service, _, client = setup
    service.reconcile_once()
    expected = published(client)["appliedImportChecksum"]
    assert expected

    revived = restart(service)
    revived.state.discovery_synced = True
    client.realm_checksum = "0" * 64
    state = revived.reconcile_once()

    # It noticed across the restart, re-applied, and put the realm back to the
    # same document -- so the checksum it expects is the one it started with.
    assert len(client.created_jobs) == 2
    assert state.applied_import_checksum == expected


def test_a_restart_keeps_a_running_grace_clock(setup):
    """Otherwise a restart hands a vanished fragment a fresh clock."""
    service, fragments, client = setup
    path = write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    path.unlink()
    service.reconcile_once()
    since = published(client)["fragments"][0]["retractingSince"]
    assert since is not None

    revived = restart(service)
    revived.state.discovery_synced = True
    state = revived.reconcile_once()

    entry = status_of(state, "scout-demo/hello")
    assert entry.status == RETRACTING
    assert entry.retracting_since == since
    assert state.phase == HOLDING


def test_a_restart_with_an_empty_fragment_dir_refuses_rather_than_retracting(setup):
    """The reconciler is up before the sidecar has written anything."""
    service, fragments, client = setup
    service.settings.retraction_grace_seconds = 0
    path = write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    jobs = dict(client.jobs)
    path.unlink()

    revived = restart(service)  # discovery_synced defaults to False
    state = revived.reconcile_once()

    assert status_of(state, "scout-demo/hello").status == RETRACTING
    assert state.phase == "Refused"
    assert client.jobs == jobs


def test_a_restart_does_not_inherit_readiness(setup):
    """The restored fact describes the last process, not this one.

    A reconciler failing on every pass would otherwise sit Ready on it, which
    under sole-writer means the deploy gate misses it entirely.
    """
    service, fragments, _ = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()

    revived = restart(service)

    assert revived.state.base_realm_applied is True
    assert revived.ready() is False

    revived.reconcile_once()

    assert revived.ready() is True


def test_a_reconciler_that_cannot_read_the_realm_goes_unready(setup):
    """A reconciler that cannot read its input has nothing to be Ready about."""
    service, fragments, client = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    assert service.ready() is True

    service.settings.base_realm_configmap = "gone"
    with pytest.raises(FileNotFoundError):
        service.reconcile_once()

    assert service.ready() is False


def test_a_refused_apply_is_not_ready(setup):
    """Refused means the realm is stale, however well the last apply went."""
    service, fragments, _ = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    assert service.ready() is True

    # A base-realm credential the client-secrets Secret does not resolve.
    path = service.settings.base_realm_path
    realm = json.loads(open(path).read())
    realm["clients"][0]["secret"] = "$(env:not_in_the_secret)"
    open(path, "w").write(json.dumps(realm))
    state = service.reconcile_once()

    assert state.phase == REFUSED
    assert service.ready() is False


def test_readiness_is_false_until_the_realm_is_applied(setup):
    service, fragments, client = setup
    service.settings.job_timeout_seconds = 5
    client.job_succeeds = False
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))

    assert service.ready() is False
    service.reconcile_once()
    assert service.ready() is False

    client.job_succeeds = True
    service.reconcile_once()
    assert service.ready() is True


def test_holding_is_not_ready(setup):
    """Holding leaves the realm alone with a change outstanding, so it is not
    "a fragment's problem" -- it is "the realm this deploy published is not in
    Keycloak", and Ready is the whole of the Flux lane's gate on that.

    This process has applied nothing at all: it came up, found a fragment
    absent with no copy to stand in for it, and stopped. Reporting Ready there
    satisfies the gate over a realm nothing has imported.
    """
    service, fragments, client = setup
    path = write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()

    path.unlink()
    revived = restart(service)
    revived.state.discovery_synced = True
    state = revived.reconcile_once()

    assert state.phase == HOLDING
    assert revived.ready() is False

    # And it clears itself: the fragment comes back, the realm applies, Ready.
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))

    assert revived.reconcile_once().phase == "Applied"
    assert revived.ready() is True


def test_readiness_ignores_a_rejected_fragment(setup):
    """One broken fragment is one service's problem, not the platform's."""
    service, fragments, _ = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    write_fragment(
        fragments, "scout-bad", "broken", fragment_yaml("broken", fullScopeAllowed=True)
    )

    state = service.reconcile_once()

    assert status_of(state, "scout-bad/broken").status != INSTALLED
    assert service.ready() is True


def test_an_unchanged_realm_does_not_report_the_previous_outcome(setup):
    service, fragments, client = setup
    body = fragment_yaml("hello")
    path = write_fragment(fragments, "scout-demo", "hello", body)
    service.reconcile_once()
    path.unlink()
    revived = restart(service)
    revived.state.discovery_synced = True
    revived.reconcile_once()
    assert "holding" in published(client)["lastResult"]

    write_fragment(fragments, "scout-demo", "hello", body)
    revived.reconcile_once()

    doc = published(client)
    assert doc["phase"] == "Applied"
    assert "holding" not in doc["lastResult"]
    assert "up to date" in doc["lastResult"]


def test_a_resumed_service_distrusts_the_published_discovery_flag(setup):
    """A previous process's sync says nothing about /fragments now."""
    service, fragments, client = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    assert published(client)["discoverySynced"] is True

    assert restart(service).state.discovery_synced is False


def test_a_document_round_trips():
    state = State(
        last_applied_hash="sha256:abc",
        base_realm_applied=True,
        identical_to_base=False,
        discovery_synced=True,
        phase=HOLDING,
        fragments=[
            FragmentStatus(
                ref="ns/name",
                namespace="ns",
                name="name",
                status=RETRACTING,
                content_hash="sha256:def",
                errors=["absent for 12s"],
                retracting_since="2026-09-01T00:00:00Z",
            )
        ],
    )

    back = from_document(to_document(state))

    assert back.last_applied_hash == "sha256:abc"
    assert back.base_realm_applied is True
    assert back.identical_to_base is state.identical_to_base
    assert back.discovery_synced is state.discovery_synced
    assert back.phase == HOLDING
    assert back.fragments[0].retracting_since == "2026-09-01T00:00:00Z"
    assert back.fragments[0].errors == ["absent for 12s"]
    # Recomputed from disk, never persisted.
    assert back.fragments[0].effect is None


def test_an_unreadable_status_is_ignored_rather_than_fatal(setup):
    service, _, client = setup
    client.configmaps[("scout-core", "scout-app-manager-status")] = {
        "data": {STATUS_KEY: "{{ not yaml"}
    }

    assert StatusStore(client, "scout-core", "scout-app-manager-status").load() is None


def test_a_corrupt_timestamp_reads_as_just_now():
    """So it can never be what retracts a live client."""
    assert age_seconds("not a timestamp") == 0.0


def test_publishing_a_new_base_realm_makes_the_pod_unready(setup):
    """What lets a deploy gate on the pod instead of on this document's shape.

    Ready has to mean "the realm you just published is in Keycloak", not "some
    realm is". Without the applied hash, an upgrade that does not restart the
    pod finds it already Ready, over a reconcile that predates the deploy.
    """
    service, fragments, _ = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    assert service.ready() is True

    path = service.settings.base_realm_path
    realm = json.loads(open(path).read())
    realm["displayName"] = "Renamed"
    open(path, "w").write(json.dumps(realm))

    # The document on the cluster has moved and nothing has applied it yet.
    service.state.base_source_hash = "sha256:" + "0" * 64
    assert service.ready() is False

    service.reconcile_once()
    assert service.ready() is True


def test_a_restart_over_a_realm_it_never_applied_is_not_ready(setup):
    """The restored facts describe a document that is no longer the one."""
    service, fragments, client = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()

    path = service.settings.base_realm_path
    realm = json.loads(open(path).read())
    realm["displayName"] = "Published while it was down"
    open(path, "w").write(json.dumps(realm))

    revived = restart(service)
    assert revived.state.applied_base_source_hash is not None
    assert revived.ready() is False

    revived.state.discovery_synced = True
    revived.reconcile_once()

    assert revived.ready() is True
