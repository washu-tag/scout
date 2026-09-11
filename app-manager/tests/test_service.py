import base64
import json

import pytest
from conftest import (  # noqa: F401
    FakeClient,
    fragment_yaml,
    restart,
    setup,
    status_of,
    write_fragment,
)

from scout_app_manager import apply, loop, main
from scout_app_manager.apply import RealmApplier, apply_job_body
from scout_app_manager.compose import SecretBinding
from scout_app_manager.loop import await_discovery
from scout_app_manager.models import (
    APPLIED,
    FAILED,
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
        client.configmaps[("scout-core", "keycloak-config-composed")]["data"][
            "scout-realm.json"
        ]
    )


def test_a_discovered_fragment_is_installed(setup):
    """Discovery is the whole gate: a valid fragment reaches the realm."""
    service, fragments, client = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))

    state = service.reconcile_once()

    assert status_of(state, "scout-demo/hello").status == INSTALLED
    assert any(c["clientId"] == "hello" for c in composed_realm(client)["clients"])


def test_an_edit_reaches_the_realm_unreviewed(setup):
    """Including one that widens a grant. Nothing stands between the two."""
    service, fragments, client = setup
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


def test_a_vanished_fragment_keeps_its_realm_objects(setup):
    """A chart upgrade's delete-then-create must not kill a live client."""
    service, fragments, client = setup
    path = write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    applied = dict(client.jobs)

    path.unlink()
    state = service.reconcile_once()

    entry = status_of(state, "scout-demo/hello")
    assert entry.status == RETRACTING
    assert entry.retracting_since is not None
    # Composed from the copy this process last applied, so the document did not
    # move and there is nothing new to apply.
    assert state.phase == APPLIED
    assert client.jobs == applied
    assert any(c["clientId"] == "hello" for c in composed_realm(client)["clients"])


def test_an_absent_fragment_does_not_freeze_the_rest_of_the_realm(setup):
    """One component redeploying used to defer every other realm change.

    Worse, a flapping fragment reset the clock, so the freeze had no bound
    while the pod went on reporting Ready.
    """
    service, fragments, client = setup
    path = write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()

    path.unlink()
    realm = json.loads(open(service.settings.base_realm_path).read())
    realm["displayName"] = "Renamed"
    open(service.settings.base_realm_path, "w").write(json.dumps(realm))
    state = service.reconcile_once()

    assert state.phase == APPLIED
    composed = composed_realm(client)
    assert composed["displayName"] == "Renamed"
    assert any(c["clientId"] == "hello" for c in composed["clients"])


@pytest.mark.parametrize("synced,phase", [(True, HOLDING), (False, REFUSED)])
def test_an_absent_fragment_with_no_composed_copy_holds_the_apply(setup, synced, phase):
    """The one absence still worth stopping for: a restart mid-redeploy.

    There is nothing to stand in for the fragment, so applying would prune its
    realm objects on the strength of one empty directory.
    """
    service, fragments, client = setup
    path = write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    applied = dict(client.jobs)

    path.unlink()
    revived = restart(service)
    revived.state.discovery_synced = synced
    state = revived.reconcile_once()

    assert status_of(state, "scout-demo/hello").status == RETRACTING
    assert state.phase == phase
    assert client.jobs == applied


def test_a_cached_copy_that_no_longer_composes_holds_the_apply(setup):
    """Applying without it is the retraction, arriving early and unannounced.

    Here a second fragment turns up claiming the absent one's clientId, which
    rejects both -- so the stand-in is gone and there is nothing to compose.
    """
    service, fragments, client = setup
    path = write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    applied = dict(client.jobs)

    path.unlink()
    write_fragment(fragments, "team-b", "hello", fragment_yaml("hello"))
    state = service.reconcile_once()

    assert state.phase == HOLDING
    assert client.jobs == applied
    assert any(
        "also declared by" in e for e in status_of(state, "scout-demo/hello").errors
    )


def test_a_fragment_that_comes_back_inside_the_grace_period_is_a_no_op(setup):
    service, fragments, client = setup
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
    """An empty fragment dir is not evidence of deletion.

    Even with the grace period elapsed: the clock only starts meaning
    something once discovery has said it looked and found nothing.
    """
    service, fragments, client = setup
    service.settings.retraction_grace_seconds = 0
    path = write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()

    service.state.discovery_synced = False
    path.unlink()
    state = service.reconcile_once()

    assert status_of(state, "scout-demo/hello").status == RETRACTING
    assert any(c["clientId"] == "hello" for c in composed_realm(client)["clients"])


@pytest.mark.parametrize("how", ["unreadable", "deleted"])
def test_a_credential_that_blips_does_not_retract_its_client(setup, how):
    """The Secret watch schedules the reconcile straight into this window.

    Rejecting the fragment drops it out of the composed realm, and the apply
    prunes what the realm does not declare -- so a delete-then-create of one
    Secret used to take a live client with it.
    """
    service, fragments, client = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()

    if how == "unreadable":
        client.unreadable_secrets.add("hello-keycloak-client")
    else:
        del client.secrets[("scout-core", "hello-keycloak-client")]
    state = service.reconcile_once()

    assert status_of(state, "scout-demo/hello").status == INSTALLED
    assert any(c["clientId"] == "hello" for c in composed_realm(client)["clients"])


def test_a_credential_gone_past_the_grace_period_rejects_its_fragment(setup):
    """Damping is a window, not a licence to run on a credential nobody has."""
    service, fragments, client = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()

    service.settings.retraction_grace_seconds = 0
    del client.secrets[("scout-core", "hello-keycloak-client")]
    state = service.reconcile_once()

    assert status_of(state, "scout-demo/hello").status == REJECTED
    assert not any(c["clientId"] == "hello" for c in composed_realm(client)["clients"])


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


def test_a_fragment_moves_the_realm_off_the_base(setup):
    service, fragments, _ = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))

    state = service.reconcile_once()

    assert state.identical_to_base is False
    assert state.base_hash != state.composed_hash


def test_a_reconcile_writes_the_composed_realm_and_runs_config_cli(setup):
    service, fragments, client = setup
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
    service.settings.job_timeout_seconds = 5
    client.job_succeeds = False
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))

    state = service.reconcile_once()

    assert state.last_applied_hash is None
    assert "apply failed" in state.last_result
    # From the Job's own conditions, and pointing at the log rather than
    # quoting it: config-cli's log is post-substitution, and this string is
    # rendered into a ConfigMap.
    assert "BackoffLimitExceeded" in state.last_result
    assert "kubectl logs" in state.last_result


def test_a_failed_apply_is_retried_rather_than_replayed(setup):
    """The Job name is the realm hash, so the retry asks for the same name.

    Keycloak was briefly down; the fragments did not change, so nothing else
    distinguishes this reconcile from the one that failed. Waiting on the
    failed Job again would report a stale failure for its whole TTL.
    """
    service, fragments, client = setup
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

    service.reconcile_once()

    # The base realm is already what is deployed, but a first reconcile still
    # applies once to establish the baseline hash.
    assert len(client.jobs) == 1
    realm = composed_realm(client)
    assert [c["clientId"] for c in realm["clients"]] == ["launchpad", "oauth2-proxy"]


# --- the realm names its credentials rather than carrying them --------------


def name_a_credential(service, variable):
    """Put a `$(env:...)` token in the base realm, as the real one has."""
    realm = json.loads(open(service.settings.base_realm_path).read())
    realm["clients"][0]["secret"] = f"$(env:{variable})"
    open(service.settings.base_realm_path, "w").write(json.dumps(realm))


def test_a_resolvable_base_realm_credential_applies(setup):
    service, _, client = setup
    name_a_credential(service, "oauth2_proxy")

    state = service.reconcile_once()

    assert state.phase == APPLIED
    # Still named in the document that reaches config-cli; the value only ever
    # exists in the Job's environment.
    assert composed_realm(client)["clients"][0]["secret"] == "$(env:oauth2_proxy)"


def test_an_unresolvable_base_realm_credential_refuses_the_whole_apply(setup):
    """config-cli would install the literal token as the client's secret.

    Unlike a fragment, a base-realm client cannot be dropped and the rest
    applied, so this stops everything.
    """
    service, _, client = setup
    name_a_credential(service, "not_in_the_secret")

    state = service.reconcile_once()

    assert state.phase == REFUSED
    assert "$(env:not_in_the_secret)" in state.last_result
    assert client.jobs == {}


def test_a_present_but_empty_credential_counts_as_unresolvable(setup):
    """An empty value is substituted, so the client gets a blank secret."""
    service, _, client = setup
    client.set_secret("scout-core", "keycloak-client-secrets", {"oauth2_proxy": ""})
    name_a_credential(service, "oauth2_proxy")

    assert service.reconcile_once().phase == REFUSED
    assert client.jobs == {}


def test_a_binary_credential_counts_as_unresolvable(setup):
    """It used to raise out of the accessor and stop every realm update."""
    service, _, client = setup
    client.set_secret("scout-core", "keycloak-client-secrets", {"oauth2_proxy": "x"})
    client.secrets[("scout-core", "keycloak-client-secrets")]["data"][
        "oauth2_proxy"
    ] = base64.b64encode(b"\xff\xfe").decode()
    name_a_credential(service, "oauth2_proxy")

    assert service.reconcile_once().phase == REFUSED
    assert client.jobs == {}


def test_the_site_hostname_needs_no_secret(setup):
    """Every base-realm URL is written against it, and it is not a credential."""
    service, _, client = setup
    realm = json.loads(open(service.settings.base_realm_path).read())
    realm["clients"][0]["redirectUris"] = ["https://x.$(env:server_hostname)/cb"]
    open(service.settings.base_realm_path, "w").write(json.dumps(realm))

    assert service.reconcile_once().phase == APPLIED
    job = next(iter(client.jobs.values()))
    env = {
        e["name"]: e for e in job["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["server_hostname"]["value"] == service.settings.domain


def test_rotating_a_credential_re_applies_an_unchanged_document(setup):
    """The document no longer moves when a credential does, so this is the
    only thing that would notice."""
    service, _, client = setup
    # reconcile_once returns the live State, so snapshot rather than compare
    # the object with itself.
    first = service.reconcile_once()
    document, version = first.composed_hash, first.secrets_version

    client.set_secret(
        "scout-core", "keycloak-client-secrets", {"oauth2_proxy": "rotated"}
    )
    second = service.reconcile_once()

    assert second.composed_hash == document
    assert second.secrets_version != version
    assert len(client.created_jobs) == 2


def test_an_untouched_credential_does_not_re_apply(setup):
    service, _, client = setup
    service.reconcile_once()
    service.reconcile_once()

    assert len(client.created_jobs) == 1


# --- where the base realm is read from ----------------------------------------


def test_the_configured_configmap_is_read_by_name(setup, base_realm):
    """Not from a mount, and not from anything selected by label: the base
    realm is applied wholesale, with none of a fragment's rails."""
    service, _, client = setup
    service.settings.base_realm_configmap = "keycloak-base-realm"
    client.configmaps[("scout-core", "keycloak-base-realm")] = {
        "data": {
            "scout-realm.json": json.dumps({**base_realm, "realm": "from-the-api"})
        }
    }

    assert service.base_realm()["realm"] == "from-the-api"


def test_the_configmap_is_re_read_every_reconcile(setup, base_realm):
    """The whole point of reading by name: no copy to go stale."""
    service, _, client = setup
    service.settings.base_realm_configmap = "keycloak-base-realm"
    key = ("scout-core", "keycloak-base-realm")
    client.configmaps[key] = {
        "data": {"scout-realm.json": json.dumps({**base_realm, "realm": "first"})}
    }
    assert service.base_realm()["realm"] == "first"

    client.configmaps[key] = {
        "data": {"scout-realm.json": json.dumps({**base_realm, "realm": "second"})}
    }
    assert service.base_realm()["realm"] == "second"


def test_a_missing_configmap_is_an_error_not_an_empty_realm(setup):
    """An empty realm would retract every client Keycloak has."""
    service, _, _ = setup
    service.settings.base_realm_configmap = "keycloak-base-realm"

    with pytest.raises(FileNotFoundError):
        service.base_realm()


def test_a_configmap_without_the_key_is_an_error(setup):
    service, _, client = setup
    service.settings.base_realm_configmap = "keycloak-base-realm"
    client.configmaps[("scout-core", "keycloak-base-realm")] = {
        "data": {"other.json": "{}"}
    }

    with pytest.raises(FileNotFoundError):
        service.base_realm()


def test_the_path_is_still_the_source_when_no_configmap_is_named(setup):
    """How the CLI reads it, where there is no cluster to ask."""
    service, _, _ = setup

    assert service.settings.base_realm_configmap == ""
    assert service.base_realm()["realm"] == "scout"


# --- what the secret watch rings for ------------------------------------------


def test_a_fragment_credential_joins_the_watched_set(setup, hello_yaml):
    """No label on the Secret: the fragment's own secretRef names it."""
    service, fragments, _ = setup
    assert "hello-keycloak-client" not in service.watched_secrets()

    write_fragment(fragments, "hello", "hello-keycloak", hello_yaml)
    service.reconcile_once()

    assert "hello-keycloak-client" in service.watched_secrets()


def test_a_fragment_whose_secret_does_not_exist_yet_is_still_watched(setup):
    """The bootstrap case, and the one the doorbell is most useful for: the
    fragment lands first and is rejected for the missing credential, so a
    binding-derived set would never wake when the Secret arrives."""
    service, fragments, client = setup
    write_fragment(
        fragments, "later", "later-keycloak", fragment_yaml("later", client="later")
    )
    state = service.reconcile_once()

    assert status_of(state, "later/later-keycloak").status == REJECTED
    assert "later-keycloak-client" in service.watched_secrets()


def test_only_the_base_realm_configmap_is_watched(setup):
    """Fragments come by the sidecar; nothing else should wake on a ConfigMap."""
    service, _, _ = setup
    assert service.watched_configmaps() == set()

    service.settings.base_realm_configmap = "keycloak-base-realm"
    assert service.watched_configmaps() == {"keycloak-base-realm"}


def test_the_platform_credentials_are_watched_before_any_reconcile(setup):
    """A rotation during startup still has to wake something."""
    service, _, _ = setup

    assert service.watched_secrets() == {
        "keycloak-client-secrets",
        "keycloak-admin-secret",
    }


def test_a_retracted_fragment_stops_being_watched(setup, hello_yaml):
    """Only once retracted: while it is composed from cache it is still ours."""
    service, fragments, _ = setup
    service.settings.retraction_grace_seconds = 0
    path = write_fragment(fragments, "hello", "hello-keycloak", hello_yaml)
    service.reconcile_once()
    path.unlink()
    service.reconcile_once()

    assert "hello-keycloak-client" not in service.watched_secrets()


# --- another writer -----------------------------------------------------------


def test_a_realm_written_by_something_else_is_re_applied(setup):
    """The two-writer window this whole design exists to close.

    `make install-auth` applies the base realm without the reconciler's rails
    and deletes every fragment-created client. Hash comparison cannot see it:
    the composed document did not move, so the reconciler used to report the
    realm up to date while a fragment's client was gone.
    """
    service, fragments, client = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    state = service.reconcile_once()
    assert state.drift is False
    assert len(client.created_jobs) == 1

    client.realm_checksum = "0" * 64  # somebody else imported a realm
    state = service.reconcile_once()

    # A successful repair clears the flag, so the re-apply is the evidence.
    assert len(client.created_jobs) == 2
    assert state.drift is False
    # The expectation came back from the realm, so this settles rather than
    # re-applying on every reconcile from here on.
    assert service.reconcile_once().drift is False
    assert len(client.created_jobs) == 2


def test_drift_is_still_reported_when_the_repair_fails(setup):
    service, _, client = setup
    service.settings.job_timeout_seconds = 5
    service.reconcile_once()

    client.realm_checksum = "0" * 64
    client.job_succeeds = False
    state = service.reconcile_once()

    assert state.drift is True
    assert state.phase == FAILED


def test_a_deleted_realm_is_re_applied(setup):
    """It used to read as "we do not know", which is the answer for a Keycloak
    that cannot be reached -- so the reconciler reported Applied and stayed
    Ready with no realm in Keycloak at all."""
    service, _, client = setup
    service.reconcile_once()

    client.realm_exists = False
    service.reconcile_once()

    # A successful repair clears the flag, so the re-apply is the evidence.
    assert len(client.created_jobs) == 2
    assert client.realm_exists is True


@pytest.mark.parametrize("how", ["deleted", "unchecksummed"])
def test_a_realm_nothing_has_imported_into_leaves_the_pod_unready(setup, how):
    service, _, client = setup
    service.settings.job_timeout_seconds = 5
    service.reconcile_once()
    assert service.ready() is True

    if how == "deleted":
        client.realm_exists = False
    else:
        client.realm_checksum = None
    client.job_succeeds = False
    service.reconcile_once()

    assert service.ready() is False


def test_an_unreadable_realm_is_not_drift(setup):
    """Not knowing must not become re-applying."""
    service, _, client = setup
    service.reconcile_once()

    service.keycloak.readable = False
    state = service.reconcile_once()

    assert state.drift is False
    assert state.live_checksum is None
    assert len(client.created_jobs) == 1


def test_an_apply_with_no_read_back_recovers_its_expectation(setup):
    """An apply whose read-back did not arrive leaves nothing to compare to.

    Nothing to compare means no drift check at all, so the first checksum that
    does arrive becomes the expectation instead of detection staying off until
    some later change happens to force another apply.
    """
    service, _, client = setup
    service.keycloak.readable = False
    service.reconcile_once()
    assert service.state.applied_import_checksum is None

    service.keycloak.readable = True
    client.realm_checksum = "0" * 64
    state = service.reconcile_once()

    assert state.drift is False
    assert state.live_checksum == "0" * 64
    assert state.applied_import_checksum == "0" * 64

    # And the check is live again: the next writer that is not us is seen, and
    # repaired inside the same reconcile.
    applied = len(client.created_jobs)
    client.realm_checksum = "1" * 64
    service.reconcile_once()

    assert len(client.created_jobs) == applied + 1


def test_a_failed_read_back_does_not_erase_a_good_expectation(setup, tmp_path):
    """Overwriting it with None would switch drift detection off for good."""
    service, fragments, _ = setup
    service.reconcile_once()
    expected = service.state.applied_import_checksum
    assert expected

    # A realm change applies, and this time the read back does not land.
    service.keycloak.readable = False
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    state = service.reconcile_once()

    assert state.applied_import_checksum == expected


def test_the_expected_checksum_is_what_config_cli_recorded(setup):
    """Read back, not computed: the checksum covers the post-substitution
    document plus a salt, and reproducing that here would be a second
    implementation of somebody else's hash."""
    service, _, client = setup

    state = service.reconcile_once()

    assert state.applied_import_checksum == client.realm_checksum
    assert state.applied_import_checksum is not None


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


def installed_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.msg.startswith("INSTALLED")]


def test_an_unchanged_fragment_is_logged_once_not_once_a_sweep(setup, caplog):
    """The resync floor is a minute, and most sweeps decide nothing new."""
    service, fragments, _ = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    with caplog.at_level("INFO"):
        for _ in range(4):
            service.reconcile_once()

    assert installed_lines(caplog) == ["INSTALLED scout-demo/hello"]


def test_an_edited_fragment_is_logged_again(setup, caplog):
    """Its content is part of the decision, so the edit says which one moved."""
    service, fragments, _ = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    with caplog.at_level("INFO"):
        service.reconcile_once()
        write_fragment(
            fragments,
            "scout-demo",
            "hello",
            fragment_yaml("hello", displayName="Hello Scout"),
        )
        service.reconcile_once()

    assert installed_lines(caplog) == ["INSTALLED scout-demo/hello"] * 2


def test_a_restart_reprints_the_fragments_it_starts_from(setup, caplog):
    """Damped per process: what this one has said, not what the realm holds."""
    service, fragments, _ = setup
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()

    with caplog.at_level("INFO"):
        restart(service).reconcile_once()

    assert installed_lines(caplog) == ["INSTALLED scout-demo/hello"]


def test_an_excluded_fragment_relogs_when_the_reason_changes(setup, caplog):
    service, fragments, _ = setup
    write_fragment(
        fragments, "scout-bad", "broken", fragment_yaml("broken", fullScopeAllowed=True)
    )
    with caplog.at_level("INFO"):
        service.reconcile_once()
        service.reconcile_once()
        write_fragment(
            fragments, "scout-bad", "broken", fragment_yaml("broken", roleClaim="sub")
        )
        service.reconcile_once()

    excluded = [r for r in caplog.records if r.msg.startswith("EXCLUDED")]
    assert len(excluded) == 2
    assert excluded[0].args != excluded[1].args


def test_settings_read_the_environment_per_process_not_per_import(monkeypatch):
    """Each Settings reads the environment. Read at import, an env var would be
    a fact about the interpreter, and one exported on a CI runner would change
    the behaviour of every test in the suite."""
    monkeypatch.setenv("APP_MANAGER_KEYCLOAK_REALM", "other")
    monkeypatch.setenv("APP_MANAGER_RESYNC_SECONDS", "42")
    settings = Settings()
    assert settings.keycloak_realm == "other"
    assert settings.resync_seconds == 42


@pytest.mark.parametrize("value,expected", [("debug", "DEBUG"), (" info ", "INFO")])
def test_the_log_level_is_normalised(monkeypatch, value, expected):
    monkeypatch.setenv("APP_MANAGER_LOG_LEVEL", value)
    assert main.log_level() == expected


def test_an_unrecognised_log_level_names_the_variable(monkeypatch):
    monkeypatch.setenv("APP_MANAGER_LOG_LEVEL", "verbose")
    with pytest.raises(SystemExit, match="APP_MANAGER_LOG_LEVEL"):
        main.log_level()


@pytest.mark.parametrize(
    "name,value", [("APP_MANAGER_RESYNC_SECONDS", "10m"), ("APP_MANAGER_PORT", "")]
)
def test_a_malformed_number_names_the_variable(monkeypatch, name, value):
    """An operator sets variables, so the message names one -- not the field."""
    monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit, match=name):
        Settings()


def job_body(**overrides):
    return apply_job_body(
        **{
            "name": "app-manager-apply-abc",
            "namespace": "scout-core",
            "image": "adorsys/keycloak-config-cli:6.5.1",
            "keycloak_url": "http://keycloak-service:8080",
            "admin_secret": "keycloak-admin-secret",
            "composed_configmap": "keycloak-config-composed",
            "client_secrets_secret": "keycloak-client-secrets",
            "server_hostname": "scout.example.edu",
            "ttl_seconds": 3600,
            **overrides,
        }
    )


def test_the_apply_job_is_rendered_from_the_yaml_resource():
    """The Job is a Kubernetes object and lives in YAML, not a Python dict."""
    body = job_body()
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
    # A ConfigMap: the composed realm names its credentials, never carries them.
    assert volume["configMap"]["name"] == "keycloak-config-composed"


def test_the_apply_job_can_resolve_what_the_realm_names():
    """Everything the composed document's `$(env:...)` tokens need."""
    container = job_body()["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e for e in container["env"]}

    # optional: the reconciler refuses the apply itself when a name does not
    # resolve, so an absent Secret must not become a pod-level failure.
    assert container["envFrom"] == [
        {"secretRef": {"name": "keycloak-client-secrets", "optional": True}}
    ]
    assert env["server_hostname"]["value"] == "scout.example.edu"
    substitution_on = json.loads(env["SPRING_APPLICATION_JSON"]["value"])["import"]
    assert substitution_on["var-substitution"]["enabled"] is True


def test_a_fragment_credential_arrives_as_its_own_secret_key_ref():
    applier = RealmApplier(Settings(), FakeClient(), "scout-core")
    body = applier._body(
        "app-manager-apply-abc",
        {
            "fragment_hello": SecretBinding(
                "fragment_hello", "hello-keycloak-client", "client-secret"
            )
        },
    )
    env = {
        e["name"]: e for e in body["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["fragment_hello"]["valueFrom"]["secretKeyRef"] == {
        "name": "hello-keycloak-client",
        "key": "client-secret",
    }


def test_the_apply_job_declares_its_prune_posture():
    """Config-cli's own default is `full` on every type."""
    body = job_body()
    container = body["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e for e in container["env"]}
    managed = json.loads(env["SPRING_APPLICATION_JSON"]["value"])["import"]["managed"]

    # Owned by the composed realm, including what its role claim depends on --
    # and the brokered IdPs, so removing one from the inventory removes it.
    for owned in (
        "client",
        "role",
        "group",
        "scope-mapping",
        "client-scope-mapping",
        "identity-provider",
    ):
        assert managed[owned] == "full", owned
    # Keycloak's own, and mostly not declared by the realm at all.
    for kept in (
        "client-scope",
        "required-action",
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


def test_a_superseded_apply_job_is_waited_out_before_the_next_one(setup, monkeypatch):
    """Two config-cli runs on one realm is the invariant ADR 0037 exists for.

    Pruning the previous Job only tells the API to delete it. Until its pod is
    gone that import is still writing, so the sweep waits -- and refuses to
    start the next one rather than overlapping with it.
    """
    monkeypatch.setattr(apply, "DELETE_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(apply, "DELETE_POLL_SECONDS", 0.0)
    service, fragments, client = setup
    service.reconcile_once()
    stale = client.created_jobs[0]
    client.lingering_jobs.add(stale)

    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    state = service.reconcile_once()

    assert stale in client.deleted_jobs
    assert client.created_jobs == [stale]
    assert state.phase == FAILED
    assert "still running" in state.last_result


def test_the_sweep_proceeds_once_the_previous_job_is_really_gone(setup, monkeypatch):
    monkeypatch.setattr(apply, "DELETE_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(apply, "DELETE_POLL_SECONDS", 0.0)
    service, fragments, client = setup
    service.reconcile_once()

    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    state = service.reconcile_once()

    assert len(client.created_jobs) == 2
    assert state.phase == APPLIED
