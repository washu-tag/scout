import json
import threading
from contextlib import contextmanager

import pytest

from scout_app_manager import watch
from scout_app_manager.k8s import ApiError


def event(kind: str, name: str, version: str = "1") -> str:
    return json.dumps(
        {
            "type": kind,
            "object": {"metadata": {"name": name, "resourceVersion": version}},
        }
    )


class FakeStream:
    """A client whose watch yields a fixed script of lines, once per call."""

    def __init__(self, lines: list[str], fail_with: Exception | None = None):
        self.lines = lines
        self.fail_with = fail_with
        self.paths: list[str] = []
        self.read_timeout: float | None = None

    @contextmanager
    def stream(self, path: str, *, read_timeout: float | None = None):
        self.paths.append(path)
        self.read_timeout = read_timeout
        if self.fail_with:
            raise self.fail_with
        yield iter(self.lines)


def watched(*names: str):
    return lambda: set(names)


def run(client, names, wake, version="", resource="secrets"):
    return watch.watch_once(client, "scout-core", resource, names, wake, version)


def test_a_watched_object_rings_the_doorbell():
    wake = threading.Event()
    client = FakeStream([event("MODIFIED", "keycloak-client-secrets")])

    run(client, watched("keycloak-client-secrets"), wake)

    assert wake.is_set()


def test_an_unwatched_object_does_not():
    """Everything in the namespace comes down the same stream; most of it is
    nothing to do with the realm."""
    wake = threading.Event()
    client = FakeStream([event("MODIFIED", "postgres-superuser")])

    run(client, watched("keycloak-client-secrets"), wake)

    assert not wake.is_set()


def test_the_interest_set_is_read_per_event_not_once_per_connection():
    """A fragment's credential joins the set on the reconcile its own ConfigMap
    event triggered, so a long-lived connection has to see the widened set."""
    wake = threading.Event()
    names = {"keycloak-client-secrets"}
    seen = []

    def widening():
        seen.append(set(names))
        return set(names)

    client = FakeStream(
        [
            event("MODIFIED", "keycloak-client-secrets"),
            event("MODIFIED", "hello-keycloak-client"),
        ]
    )

    def names_fn():
        current = widening()
        names.add("hello-keycloak-client")
        return current

    run(client, names_fn, wake)

    # Both events consulted the callable, and the second saw the wider set.
    assert len(seen) == 2
    assert "hello-keycloak-client" in seen[1]


def test_the_replay_after_a_reset_does_not_wake():
    """A fresh connection replays the whole collection as synthetic ADDED.
    Waking on those turns a flapping connection into a reconcile every few
    seconds."""
    wake = threading.Event()
    client = FakeStream([event("ADDED", "keycloak-client-secrets")])

    run(client, watched("keycloak-client-secrets"), wake)

    assert not wake.is_set()


def test_a_creation_on_an_established_connection_does_wake():
    """Only the opening replay is ignored, not every ADDED forever."""
    wake = threading.Event()
    client = FakeStream([event("ADDED", "hello-keycloak-client", "9")])

    run(client, watched("hello-keycloak-client"), wake, version="8")

    assert wake.is_set()


def test_a_bookmark_advances_the_version_without_waking():
    wake = threading.Event()
    client = FakeStream([event("BOOKMARK", "keycloak-client-secrets", "77")])

    version = run(client, watched("keycloak-client-secrets"), wake)

    assert version == "77"
    assert not wake.is_set()


def test_the_version_is_resumed_from_on_the_next_connection():
    wake = threading.Event()
    client = FakeStream([event("MODIFIED", "x", "42")])

    version = run(client, watched(), wake)
    run(client, watched(), wake, version)

    assert "resourceVersion" not in client.paths[0]
    assert "resourceVersion=42" in client.paths[1]


def test_the_resource_kind_selects_the_collection():
    wake = threading.Event()
    client = FakeStream([])

    run(client, watched(), wake, resource="configmaps")

    assert "/namespaces/scout-core/configmaps?" in client.paths[0]


def test_the_connection_is_bounded_at_both_ends():
    """A half-open connection is a doorbell that has stopped ringing, and with
    no read timeout nothing notices for hours."""
    wake = threading.Event()
    client = FakeStream([])

    run(client, watched(), wake)

    assert f"timeoutSeconds={watch.SERVER_TIMEOUT_SECONDS}" in client.paths[0]
    assert client.read_timeout > watch.SERVER_TIMEOUT_SECONDS


def test_an_in_band_error_is_raised_so_the_caller_starts_over():
    """A resourceVersion too old arrives as an event, not an HTTP status."""
    wake = threading.Event()
    client = FakeStream(
        [json.dumps({"type": "ERROR", "object": {"code": 410, "message": "too old"}})]
    )

    with pytest.raises(ApiError):
        run(client, watched(), wake)


def test_an_unparsable_line_is_skipped_not_fatal():
    wake = threading.Event()
    client = FakeStream(["{not json", "", event("MODIFIED", "wanted")])

    run(client, watched("wanted"), wake)

    assert wake.is_set()


# --- the runner ---------------------------------------------------------------


def drive(monkeypatch, outcomes):
    """Run the loop over a script of per-attempt outcomes, recording sleeps."""
    attempts, sleeps = [], []

    def fake_watch_once(client, namespace, resource, names, wake, version=""):
        attempts.append(version)
        outcome = outcomes[len(attempts) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(watch, "watch_once", fake_watch_once)
    monkeypatch.setattr(watch.shutdown, "sleep", lambda s: sleeps.append(s) or True)
    with pytest.raises(SystemExit):
        watch.run_forever(
            FakeStream([]), "scout-core", "secrets", watched(), threading.Event()
        )
    return attempts, sleeps


def test_a_dropped_connection_restarts_without_the_stale_version(monkeypatch):
    """The doorbell is allowed to be lossy; what it must not do is wedge on a
    version the server has forgotten."""
    attempts, _ = drive(monkeypatch, ["99", ApiError(410, "too old"), SystemExit()])

    assert attempts == ["", "99", ""]


def test_a_transport_error_does_not_kill_the_thread(monkeypatch):
    """The failures that actually happen are connection resets, not ApiError."""
    attempts, _ = drive(monkeypatch, [OSError("connection reset"), SystemExit()])

    assert attempts == ["", ""]


def test_a_clean_return_still_pauses_so_a_closing_server_cannot_spin(monkeypatch):
    _, sleeps = drive(monkeypatch, ["7", SystemExit()])

    assert sleeps == [watch.BACKOFF_SECONDS]


def test_repeated_failure_backs_off(monkeypatch):
    """A permanent 403 -- RBAC not applied yet -- must not retry at 5s forever."""
    _, sleeps = drive(
        monkeypatch,
        [ApiError(403, "forbidden")] * 3 + [SystemExit()],
    )

    assert sleeps == [
        watch.BACKOFF_SECONDS,
        watch.BACKOFF_SECONDS * 2,
        watch.BACKOFF_SECONDS * 4,
    ]


def test_the_backoff_resets_after_a_good_connection(monkeypatch):
    _, sleeps = drive(
        monkeypatch,
        [ApiError(403, "forbidden"), ApiError(403, "forbidden"), "5", SystemExit()],
    )

    assert sleeps[-1] == watch.BACKOFF_SECONDS
