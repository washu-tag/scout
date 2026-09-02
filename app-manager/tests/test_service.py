import json

import pytest
from conftest import fragment_yaml, setup, status_of, write_fragment  # noqa: F401

from scout_app_manager import apply, loop
from scout_app_manager.apply import apply_job_body
from scout_app_manager.loop import await_discovery
from scout_app_manager.models import (
    APPLIED,
    HOLDING,
    INSTALLED,
    INVALID,
    REFUSED,
    REJECTED,
    RETRACTING,
)
from scout_app_manager.settings import Settings


def composed_realm(client) -> dict:
    return json.loads(
        client.secrets[("scout-core", "keycloak-config-composed")]["plain"][
            "scout-realm.json"
        ]
    )


def test_a_discovered_fragment_is_installed(setup):
    """Discovery is the whole gate: a valid fragment reaches the realm."""
    service, fragments, client = setup
    service.settings.apply_mode = "apply"
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))

    state = service.reconcile_once()

    assert status_of(state, "scout-demo/hello").status == INSTALLED
    assert any(c["clientId"] == "hello" for c in composed_realm(client)["clients"])


def test_an_edit_reaches_the_realm_unreviewed(setup):
    """Including one that widens a grant. Nothing stands between the two."""
    service, fragments, client = setup
    service.settings.apply_mode = "apply"
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()

    write_fragment(
        fragments,
        "scout-demo",
        "hello",
        fragment_yaml(
            "hello",
            grants={
                "scout-user": ["hello-user", "hello-admin"],
                "scout-admin": ["hello-admin"],
            },
        ),
    )
    state = service.reconcile_once()

    assert status_of(state, "scout-demo/hello").status == INSTALLED
    scout_user = next(
        g for g in composed_realm(client)["groups"] if g["name"] == "scout-user"
    )
    assert scout_user["clientRoles"]["hello"] == ["hello-user", "hello-admin"]


def test_an_invalid_fragment_is_excluded(setup):
    service, fragments, _ = setup
    write_fragment(
        fragments,
        "scout-bad",
        "broken",
        fragment_yaml("broken", fullScopeAllowed=True),
    )

    state = service.reconcile_once()

    assert status_of(state, "scout-bad/broken").status == INVALID
    assert state.identical_to_base is True


def test_deleting_the_fragment_removes_everything(setup):
    """With no grace period, absence retracts immediately."""
    service, fragments, client = setup
    service.settings.apply_mode = "apply"
    service.settings.retraction_grace_seconds = 0
    path = write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()

    path.unlink()
    state = service.reconcile_once()

    assert state.fragments == []
    assert state.phase == APPLIED
    realm = composed_realm(client)
    assert not any(c["clientId"] == "hello" for c in realm["clients"])
    assert (
        "hello"
        not in next(g for g in realm["groups"] if g["name"] == "scout-user")[
            "clientRoles"
        ]
    )


def test_a_vanished_fragment_is_held_before_it_is_retracted(setup):
    """A chart upgrade's delete-then-create must not kill a live client."""
    service, fragments, client = setup
    service.settings.apply_mode = "apply"
    path = write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    applied = dict(client.jobs)

    path.unlink()
    state = service.reconcile_once()

    entry = status_of(state, "scout-demo/hello")
    assert entry.status == RETRACTING
    assert entry.retracting_since is not None
    assert state.phase == HOLDING
    # No new apply, and the last applied document still carries the client.
    assert client.jobs == applied
    assert any(c["clientId"] == "hello" for c in composed_realm(client)["clients"])


def test_a_fragment_that_comes_back_inside_the_grace_period_is_a_no_op(setup):
    service, fragments, client = setup
    service.settings.apply_mode = "apply"
    body = fragment_yaml("hello")
    path = write_fragment(fragments, "scout-demo", "hello", body)
    service.reconcile_once()
    applied = dict(client.jobs)

    path.unlink()
    service.reconcile_once()
    write_fragment(fragments, "scout-demo", "hello", body)
    state = service.reconcile_once()

    assert status_of(state, "scout-demo/hello").status == INSTALLED
    assert state.phase == APPLIED
    # Nothing applied across the whole episode.
    assert client.jobs == applied


def test_nothing_is_retracted_until_discovery_reports_a_sync(setup):
    """An empty fragment dir is not evidence of deletion."""
    service, fragments, client = setup
    service.settings.apply_mode = "apply"
    service.settings.retraction_grace_seconds = 0
    path = write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    applied = dict(client.jobs)

    service.state.discovery_synced = False
    path.unlink()
    state = service.reconcile_once()

    assert status_of(state, "scout-demo/hello").status == RETRACTING
    assert state.phase == REFUSED
    assert client.jobs == applied
    assert any(c["clientId"] == "hello" for c in composed_realm(client)["clients"])


def test_a_rejected_fragment_going_absent_retracts_nothing(setup):
    """It never reached the realm."""
    service, fragments, _ = setup
    path = write_fragment(
        fragments, "evil", "takeover", fragment_yaml("launchpad", roles=[], grants={})
    )
    assert status_of(service.reconcile_once(), "evil/takeover").status == REJECTED

    path.unlink()
    state = service.reconcile_once()

    assert state.fragments == []
    assert state.phase != HOLDING


def test_a_colliding_fragment_shows_as_rejected(setup):
    service, fragments, _ = setup
    write_fragment(
        fragments, "evil", "takeover", fragment_yaml("launchpad", roles=[], grants={})
    )

    fragment = status_of(service.reconcile_once(), "evil/takeover")

    assert fragment.status == REJECTED
    assert "already exists in the base realm" in " ".join(fragment.errors)


def test_no_fragments_composes_to_the_base_realm_byte_for_byte(setup):
    """The invariant a first deploy has to satisfy, reported explicitly."""
    service, _, _ = setup

    state = service.reconcile_once()

    assert state.identical_to_base is True
    assert state.base_hash == state.composed_hash
    assert "byte-identical" in state.last_result


def test_a_fragment_moves_the_realm_off_the_base(setup):
    service, fragments, _ = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))

    state = service.reconcile_once()

    assert state.identical_to_base is False
    assert state.base_hash != state.composed_hash


def test_diff_mode_never_writes(setup):
    service, fragments, client = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))

    state = service.reconcile_once()

    assert client.jobs == {}
    assert ("scout-core", "keycloak-config-composed") not in client.secrets
    assert state.pending_change is True
    assert "diff mode" in state.last_result


def test_apply_mode_writes_the_composed_realm_and_runs_config_cli(setup):
    service, fragments, client = setup
    service.settings.apply_mode = "apply"
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))

    state = service.reconcile_once()

    assert any(c["clientId"] == "hello" for c in composed_realm(client)["clients"])
    assert len(client.jobs) == 1
    job = next(iter(client.jobs.values()))
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == service.settings.config_cli_image
    assert state.last_applied_hash is not None
    assert "applied" in state.last_result


def test_a_failed_apply_does_not_record_the_realm_as_applied(setup):
    service, fragments, client = setup
    service.settings.apply_mode = "apply"
    service.settings.job_timeout_seconds = 5
    client.job_succeeds = False
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))

    state = service.reconcile_once()

    assert state.last_applied_hash is None
    assert "apply failed" in state.last_result


def test_a_failed_apply_is_retried_rather_than_replayed(setup):
    """The Job name is the realm hash, so the retry asks for the same name.

    Keycloak was briefly down; the fragments did not change, so nothing else
    distinguishes this reconcile from the one that failed. Waiting on the
    failed Job again would report a stale failure for its whole TTL.
    """
    service, fragments, client = setup
    service.settings.apply_mode = "apply"
    service.settings.job_timeout_seconds = 5
    client.job_succeeds = False
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    assert service.reconcile_once().last_applied_hash is None

    client.job_succeeds = True
    state = service.reconcile_once()

    assert state.phase == APPLIED
    assert state.last_applied_hash is not None
    # The same name twice: the stale Job was deleted, not waited on.
    assert len(client.created_jobs) == 2
    assert len(set(client.created_jobs)) == 1


def test_a_still_running_apply_job_is_waited_on_not_replaced(setup, monkeypatch):
    """The 409 the create is allowed to swallow: someone else is mid-apply."""
    service, _, client = setup
    monkeypatch.setattr(apply.time, "sleep", lambda _: None)
    realm_hash = "sha256:abcdef123456" + "0" * 52
    name = "app-manager-apply-abcdef123456"
    client.jobs[name] = {"metadata": {"name": name}}
    client.job_status[name] = {}  # created, no verdict yet

    polls = []
    running = client.get_job

    def get_job(namespace, job_name):
        polls.append(job_name)
        if len(polls) > 2:
            client.job_status[name] = {"succeeded": 1}
        return running(namespace, job_name)

    client.get_job = get_job
    ok, detail = service.applier.run(realm_hash)

    assert (ok, detail) == (True, "succeeded")
    assert client.deleted_jobs == []
    assert client.created_jobs == []


def test_an_empty_fragment_dir_still_applies_the_base_realm(setup):
    service, _, client = setup
    service.settings.apply_mode = "apply"

    service.reconcile_once()

    # The base realm is already what is deployed, but a first reconcile still
    # applies once to establish the baseline hash.
    assert len(client.jobs) == 1
    realm = composed_realm(client)
    assert [c["clientId"] for c in realm["clients"]] == ["launchpad", "oauth2-proxy"]


def test_a_fragment_carries_its_reported_effect(setup):
    service, fragments, _ = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    status = status_of(service.reconcile_once(), "scout-demo/hello")

    assert [c.client_id for c in status.effect.clients] == ["hello"]
    assert status.effect.clients[0].grants == {
        "scout-user": ["hello-user"],
        "scout-admin": ["hello-admin"],
    }


def test_the_app_label_comes_from_the_declared_display_name(setup):
    service, fragments, _ = setup
    write_fragment(
        fragments,
        "scout-demo",
        "hello-keycloak",
        fragment_yaml("hello", displayName="Hello Scout"),
    )
    assert (
        status_of(service.reconcile_once(), "scout-demo/hello-keycloak").display_name
        == "Hello Scout"
    )


def test_the_app_label_falls_back_to_the_configmap_name(setup):
    """No displayName declared, so the conventional suffix is dropped instead."""
    service, fragments, _ = setup
    write_fragment(fragments, "scout-demo", "hello-keycloak", fragment_yaml("hello"))
    assert (
        status_of(service.reconcile_once(), "scout-demo/hello-keycloak").display_name
        == "hello"
    )


def test_settings_default_to_diff_mode(monkeypatch):
    monkeypatch.delenv("APP_MANAGER_APPLY_MODE", raising=False)
    assert Settings().apply_mode == "diff"


def test_settings_read_the_environment_per_process_not_per_import(monkeypatch):
    """Each Settings reads the environment. Read at import, an env var would be
    a fact about the interpreter, and one exported on a CI runner would change
    the behaviour of every test in the suite."""
    monkeypatch.setenv("APP_MANAGER_APPLY_MODE", "apply")
    monkeypatch.setenv("APP_MANAGER_RESYNC_SECONDS", "42")
    settings = Settings()
    assert settings.apply_mode == "apply"
    assert settings.resync_seconds == 42


@pytest.mark.parametrize("mode", ["Apply", "apply ", "applied", "", "true"])
def test_an_unrecognised_apply_mode_is_refused(monkeypatch, mode):
    """It used to mean diff, silently — and diff mode reports Ready.

    So `applyMode: Apply` in an inventory produced a pod that passed its
    readiness probe, satisfied helm's wait, reported phase Pending, and never
    wrote a realm.
    """
    monkeypatch.setenv("APP_MANAGER_APPLY_MODE", mode)
    with pytest.raises(SystemExit, match="APP_MANAGER_APPLY_MODE"):
        Settings()


@pytest.mark.parametrize(
    "name,value", [("APP_MANAGER_RESYNC_SECONDS", "10m"), ("APP_MANAGER_PORT", "")]
)
def test_a_malformed_number_names_the_variable(monkeypatch, name, value):
    """An operator sets variables, so the message names one -- not the field."""
    monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit, match=name):
        Settings()


def test_the_apply_job_is_rendered_from_the_yaml_resource():
    """The Job is a Kubernetes object and lives in YAML, not a Python dict."""
    body = apply_job_body(
        name="app-manager-apply-abc",
        namespace="scout-core",
        image="adorsys/keycloak-config-cli:6.5.1",
        keycloak_url="http://keycloak-service:8080",
        admin_secret="keycloak-admin-secret",
        composed_secret="keycloak-config-composed",
        ttl_seconds=3600,
    )
    assert body["kind"] == "Job"
    assert body["metadata"]["name"] == "app-manager-apply-abc"
    assert body["spec"]["backoffLimit"] == 0
    container = body["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "adorsys/keycloak-config-cli:6.5.1"
    env = {e["name"]: e for e in container["env"]}
    assert env["KEYCLOAK_URL"]["value"] == "http://keycloak-service:8080"
    # The admin credential is a secretKeyRef, never an inline value.
    assert env["KEYCLOAK_PASSWORD"]["valueFrom"]["secretKeyRef"]["key"] == "password"
    volume = body["spec"]["template"]["spec"]["volumes"][0]
    assert volume["secret"]["secretName"] == "keycloak-config-composed"


def test_the_apply_job_declares_its_prune_posture():
    """Config-cli's own default is `full` on every type."""
    body = apply_job_body(
        name="app-manager-apply-abc",
        namespace="scout-core",
        image="adorsys/keycloak-config-cli:6.5.1",
        keycloak_url="http://keycloak-service:8080",
        admin_secret="keycloak-admin-secret",
        composed_secret="keycloak-config-composed",
        ttl_seconds=3600,
    )
    container = body["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e for e in container["env"]}
    managed = json.loads(env["SPRING_APPLICATION_JSON"]["value"])["import"]["managed"]

    # Owned by the composed realm, including what its role claim depends on.
    for owned in (
        "client",
        "role",
        "group",
        "scope-mapping",
        "client-scope-mapping",
    ):
        assert managed[owned] == "full", owned
    # Keycloak's own, or provisioned outside the artifact.
    for kept in (
        "client-scope",
        "required-action",
        "identity-provider",
        "identity-provider-mapper",
        "authentication-flow",
        "component",
        "sub-component",
    ):
        assert managed[kept] == "no-delete", kept


def test_an_unsubstituted_placeholder_is_an_error():
    with pytest.raises(KeyError):
        apply_job_body(name="x", namespace="scout-core")


def test_it_waits_for_the_sidecar_before_the_first_reconcile(setup, monkeypatch):
    """A restart must not retract clients while discovery is still syncing."""
    service, _, _ = setup
    service.settings.discovery_health_url = "http://127.0.0.1:8081/healthz"
    service.settings.discovery_wait_seconds = 5
    calls = []

    class Response:
        status_code = 200

    def fake_get(url, timeout=None):
        calls.append(url)
        return Response()

    monkeypatch.setattr(loop.httpx, "get", fake_get)
    assert await_discovery(service.settings) is True
    assert calls == ["http://127.0.0.1:8081/healthz"]


def test_a_sidecar_that_never_reports_ready_does_not_block_forever(setup, monkeypatch):
    service, _, _ = setup
    service.settings.discovery_health_url = "http://127.0.0.1:8081/healthz"
    service.settings.discovery_wait_seconds = 1

    def fake_get(url, timeout=None):
        raise loop.httpx.HTTPError("nope")

    monkeypatch.setattr(loop.httpx, "get", fake_get)
    assert await_discovery(service.settings) is False


def test_the_wait_is_skipped_when_there_is_no_sidecar(setup):
    service, _, _ = setup
    service.settings.discovery_health_url = ""
    assert await_discovery(service.settings) is True
