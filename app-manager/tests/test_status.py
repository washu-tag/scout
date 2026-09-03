import yaml
from conftest import (  # noqa: F401
    FakeKeycloak,
    fragment_yaml,
    setup,
    status_of,
    write_fragment,
)

from scout_app_manager.models import (
    HOLDING,
    INSTALLED,
    RETRACTING,
    FragmentStatus,
    State,
)
from scout_app_manager.service import AppManagerService
from scout_app_manager.status import (
    STATUS_KEY,
    StatusStore,
    age_seconds,
    from_document,
    to_document,
)


def published(client) -> dict:
    data = client.configmaps[("scout-core", "scout-app-manager-status")]["data"]
    return yaml.safe_load(data[STATUS_KEY])


def restart(service) -> AppManagerService:
    """A fresh process over the same cluster."""
    return AppManagerService(
        service.settings, service.client, keycloak=FakeKeycloak(service.client)
    )


def test_the_document_is_camel_cased_and_carries_every_fragment(setup):
    service, fragments, client = setup
    service.settings.apply_mode = "apply"
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


def test_a_failed_apply_is_published_as_failed(setup):
    service, fragments, client = setup
    service.settings.apply_mode = "apply"
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
    service.settings.apply_mode = "apply"
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
    service.settings.apply_mode = "apply"
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
    service.settings.apply_mode = "apply"
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
    service.settings.apply_mode = "apply"
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


def test_diff_mode_readiness_is_about_this_process_not_the_realm(setup):
    service, _, client = setup

    assert service.ready() is False
    service.reconcile_once()

    assert service.ready() is True
    assert published(client)["baseRealmApplied"] is False


def test_a_restart_restores_readiness(setup):
    service, fragments, _ = setup
    service.settings.apply_mode = "apply"
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()

    revived = restart(service)

    assert revived.ready() is True


def test_readiness_is_false_until_the_realm_is_applied(setup):
    service, fragments, client = setup
    service.settings.apply_mode = "apply"
    service.settings.job_timeout_seconds = 5
    client.job_succeeds = False
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))

    assert service.ready() is False
    service.reconcile_once()
    assert service.ready() is False

    client.job_succeeds = True
    service.reconcile_once()
    assert service.ready() is True


def test_readiness_ignores_a_rejected_fragment(setup):
    """One broken fragment is one service's problem, not the platform's."""
    service, fragments, _ = setup
    service.settings.apply_mode = "apply"
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    write_fragment(
        fragments, "scout-bad", "broken", fragment_yaml("broken", fullScopeAllowed=True)
    )

    state = service.reconcile_once()

    assert status_of(state, "scout-bad/broken").status != INSTALLED
    assert service.ready() is True


def test_an_unchanged_realm_does_not_report_the_previous_outcome(setup):
    service, fragments, client = setup
    service.settings.apply_mode = "apply"
    body = fragment_yaml("hello")
    path = write_fragment(fragments, "scout-demo", "hello", body)
    service.reconcile_once()
    path.unlink()
    service.reconcile_once()
    assert "holding" in published(client)["lastResult"]

    write_fragment(fragments, "scout-demo", "hello", body)
    service.reconcile_once()

    doc = published(client)
    assert doc["phase"] == "Applied"
    assert "holding" not in doc["lastResult"]
    assert "up to date" in doc["lastResult"]


def test_a_resumed_service_distrusts_the_published_discovery_flag(setup):
    """A previous process's sync says nothing about /fragments now."""
    service, fragments, client = setup
    service.settings.apply_mode = "apply"
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
