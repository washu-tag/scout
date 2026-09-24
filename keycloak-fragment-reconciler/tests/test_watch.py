"""The watch, which is the doorbell and nothing more.

The property that matters is that a real creation rings the bell. A watch opened
with no resourceVersion replays the collection as synthetic ADDED events, and
suppressing ADDED to avoid waking on those silently eats every genuine
creation -- so the watch starts from a version instead.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager

import pytest

from scout_keycloak_fragment_reconciler import shutdown, watch
from scout_keycloak_fragment_reconciler.k8s import ApiError

SELECTOR = "keycloak.scout.xnat.org/fragment=true"


@pytest.fixture(autouse=True)
def _unset_shutdown():
    shutdown.requested.clear()
    yield
    shutdown.requested.clear()


def event(kind: str, name: str, version: str, namespace: str = "demo") -> str:
    return json.dumps(
        {
            "type": kind,
            "object": {
                "metadata": {
                    "name": name,
                    "namespace": namespace,
                    "resourceVersion": version,
                }
            },
        }
    )


class FakeStreamClient:
    """A client whose watch yields a scripted set of lines, once."""

    def __init__(self, lines: list[str], *, version: str = "100", error=None):
        self.lines = lines
        self.version = version
        self.error = error
        self.paths: list[str] = []
        self.version_reads = 0

    def collection_version(self, label_selector: str) -> str:
        self.version_reads += 1
        return self.version

    @contextmanager
    def stream(self, path: str, *, read_timeout=None):
        self.paths.append(path)
        if self.error is not None:
            raise self.error
        yield iter(self.lines)


def run(
    lines: list[str], **kwargs
) -> tuple[threading.Event, list, FakeStreamClient, str]:
    client = FakeStreamClient(lines, **kwargs)
    wake = threading.Event()
    witnessed: list[tuple[str, str]] = []
    version = watch.watch_once(
        client, SELECTOR, wake, lambda ns, name: witnessed.append((ns, name))
    )
    return wake, witnessed, client, version


class TestARealCreationRingsTheBell:

    def test_an_added_event_wakes_the_reconciler(self):
        wake, _, _, _ = run([event("ADDED", "hello-scout-keycloak", "101")])
        assert wake.is_set(), "a fragment was created and the watch stayed silent"

    def test_it_wakes_on_the_very_first_event_of_a_fresh_connection(self):
        """A brand-new process, one fragment installed, nothing else."""
        wake, _, client, _ = run([event("ADDED", "first-ever", "101")])
        assert client.version_reads == 1, "did not take a version before watching"
        assert wake.is_set()

    def test_modified_and_deleted_wake_it_too(self):
        for kind in ("MODIFIED", "DELETED"):
            wake, _, _, _ = run([event(kind, "hello-scout-keycloak", "101")])
            assert wake.is_set(), kind


class TestWatchingFromAVersion:
    def test_a_fresh_watch_takes_a_version_first(self):
        _, _, client, _ = run([], version="500")
        assert client.version_reads == 1
        assert "resourceVersion=500" in client.paths[0]

    def test_a_resumed_watch_reuses_the_caller_s_version(self):
        client = FakeStreamClient([], version="500")
        watch.watch_once(client, SELECTOR, threading.Event(), lambda *a: None, "321")
        assert client.version_reads == 0, "re-listed instead of resuming"
        assert "resourceVersion=321" in client.paths[0]

    def test_it_returns_the_latest_version_seen(self):
        _, _, _, version = run(
            [event("ADDED", "a", "101"), event("MODIFIED", "a", "102")]
        )
        assert version == "102"

    def test_the_label_selector_is_sent(self):
        _, _, client, _ = run([])
        assert "labelSelector=keycloak.scout.xnat.org" in client.paths[0]

    def test_bookmarks_are_requested_and_do_not_wake(self):
        wake, _, client, version = run([event("BOOKMARK", "", "150")])
        assert "allowWatchBookmarks=true" in client.paths[0]
        assert not wake.is_set()
        # But they do advance the resume point, which is what they are for.
        assert version == "150"


class TestWitnessedDeletions:
    """The watch's one extra power: naming a deletion it actually saw.

    It may never conclude absence -- that needs an authoritative list -- but an
    observed deletion is unambiguous, and reporting it is what lets GC work with
    the periodic resync disabled.
    """

    def test_a_deletion_is_reported_with_its_identity(self):
        _, witnessed, _, _ = run([event("DELETED", "hello-scout-keycloak", "101")])
        assert witnessed == [("demo", "hello-scout-keycloak")]

    def test_other_event_kinds_are_not_reported_as_deletions(self):
        for kind in ("ADDED", "MODIFIED"):
            _, witnessed, _, _ = run([event(kind, "hello-scout-keycloak", "101")])
            assert witnessed == [], kind

    def test_an_object_with_no_identity_is_skipped(self):
        line = json.dumps({"type": "DELETED", "object": {"metadata": {}}})
        wake, witnessed, _, _ = run([line])
        assert witnessed == []
        assert not wake.is_set()


class TestStreamProblems:
    def test_an_error_event_raises_so_the_caller_restarts(self):
        line = json.dumps(
            {"type": "ERROR", "object": {"code": 410, "message": "too old"}}
        )
        with pytest.raises(ApiError) as info:
            run([line])
        assert info.value.status == 410

    def test_unparsable_lines_are_ignored(self):
        wake, _, _, _ = run(["{not json", "", event("ADDED", "a", "101")])
        assert wake.is_set()

    def test_a_transport_failure_propagates(self):
        with pytest.raises(ApiError):
            run([], error=ApiError(500, "boom"))


class FlappingClient(FakeStreamClient):
    """Loses the first connection, then hands back an empty stream and stops."""

    def __init__(self):
        super().__init__([])
        self.connections = 0

    @contextmanager
    def stream(self, path: str, *, read_timeout=None):
        self.connections += 1
        if self.connections == 1:
            raise ApiError(500, "connection reset by peer")
        shutdown.requested.set()
        yield iter(())


class TestAReconnectMayHaveMissedSomething:
    """A watch reopened at the collection's current version replays nothing.

    Whatever happened during the outage is not redelivered, so the reconnect
    itself has to ring the bell: with the periodic resync disabled nothing else
    ever will, and the change waits for a pod restart.
    """

    def test_a_dropped_watch_wakes_the_reconciler(self):
        client = FlappingClient()
        wake = threading.Event()
        watch.run_forever(client, SELECTOR, wake, lambda *a: None, backoff=0)
        assert client.connections == 2, "never reconnected"
        assert wake.is_set(), "reconnected past the gap with no reconcile pass"
