import socket
import threading
import time

import httpx2 as httpx
import pytest
from conftest import fragment_yaml, setup, write_fragment  # noqa: F401

from scout_app_manager import api, health, loop, metrics
from scout_app_manager.models import RETRACTING, FragmentStatus


@pytest.fixture
def reload_server():
    """A real socket: the sidecar talks HTTP, not Python."""
    wake = threading.Event()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = api.ThreadingHTTPServer((api.LOOPBACK, port), api.handler_for(wake))
    # shutdown() waits out one poll interval, and the default is 0.5s a fixture.
    threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
    yield f"http://127.0.0.1:{port}", wake
    server.shutdown()
    server.server_close()


def test_a_post_from_the_sidecar_wakes_the_reconcile(reload_server):
    base, wake = reload_server

    response = httpx.post(f"{base}/reload", timeout=5.0)

    assert response.status_code == 202
    assert wake.is_set()


def test_a_post_carrying_a_payload_does_not_desynchronise_the_connection(
    reload_server,
):
    """The sidecar sends one whenever REQ_PAYLOAD is set.

    Keep-alive means an unread body is parsed as the next request line: 400s,
    a socket hung until the sidecar's REQ_TIMEOUT, and reloads dropped.
    """
    base, wake = reload_server

    with httpx.Client(timeout=5.0) as client:
        first = client.post(f"{base}/reload", json={"eventType": "CREATE"})
        wake.clear()
        second = client.post(f"{base}/reload", json={"eventType": "MODIFY"})

    assert (first.status_code, second.status_code) == (202, 202)
    assert wake.is_set()


def test_the_reload_endpoint_binds_to_loopback_only():
    """It can cause a realm write."""
    assert api.LOOPBACK == "127.0.0.1"


def test_a_get_is_refused_so_a_misconfigured_req_method_is_visible(reload_server):
    base, wake = reload_server

    assert httpx.get(f"{base}/reload", timeout=5.0).status_code == 405
    assert not wake.is_set()


def test_an_unknown_path_does_not_wake_anything(reload_server):
    base, wake = reload_server

    assert httpx.post(f"{base}/nope", timeout=5.0).status_code == 404
    assert not wake.is_set()


@pytest.fixture
def health_server():
    ready = {"value": False}
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = health.ThreadingHTTPServer(
        ("127.0.0.1", port),
        health.handler_for(
            lambda: ready["value"], lambda: "scout_app_manager_fragments 0\n"
        ),
    )
    threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
    yield f"http://127.0.0.1:{port}", ready
    server.shutdown()
    server.server_close()


def test_liveness_is_up_before_readiness_is(health_server):
    base, ready = health_server

    assert httpx.get(f"{base}/healthz", timeout=5.0).status_code == 200
    assert httpx.get(f"{base}/readyz", timeout=5.0).status_code == 503

    ready["value"] = True
    assert httpx.get(f"{base}/readyz", timeout=5.0).status_code == 200


def test_metrics_are_served_on_the_pod_ip_listener(health_server):
    """Prometheus reaches the pod IP, not loopback."""
    base, _ = health_server

    response = httpx.get(f"{base}/metrics", timeout=5.0)

    assert response.status_code == 200
    # The client library's own, so the declared version tracks what it emits.
    assert response.headers["content-type"] == metrics.CONTENT_TYPE_LATEST
    assert "scout_app_manager_fragments" in response.text


def test_the_wait_shrinks_to_a_held_fragments_grace_expiry(setup):  # noqa: F811
    """Otherwise a 300s grace under a 600s floor is really 600s."""
    service, fragments, _ = setup
    service.settings.resync_seconds = 600
    service.settings.retraction_grace_seconds = 300
    service.state.discovery_synced = True
    path = write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    assert loop.next_wait(service, service.settings) == 600

    path.unlink()
    service.reconcile_once()

    # Just stamped, so ~the full grace remains, not the floor.
    assert 295 <= loop.next_wait(service, service.settings) <= 301


def test_the_wait_never_busy_loops_on_an_expired_grace(setup):  # noqa: F811
    service, _, _ = setup
    service.settings.resync_seconds = 600
    service.settings.retraction_grace_seconds = 0
    service.state.discovery_synced = True
    service.state.fragments = [
        FragmentStatus(
            ref="ns/x",
            namespace="ns",
            name="x",
            status=RETRACTING,
            content_hash="sha256:x",
            retracting_since="2020-01-01T00:00:00Z",
        )
    ]

    assert loop.next_wait(service, service.settings) == loop.MINIMUM_WAIT


def test_an_unsynced_sidecar_is_re_checked_sooner_than_the_floor(setup):  # noqa: F811
    service, _, _ = setup
    service.settings.resync_seconds = 600
    service.state.discovery_synced = False

    assert loop.next_wait(service, service.settings) == loop.DEGRADED_INTERVAL


class Recorder:
    """A service stand-in that counts reconciles and stops the loop."""

    def __init__(self, state, stop_after: int):
        self.state = state
        self.stop_after = stop_after
        self.calls = 0

    def reconcile_once(self):
        self.calls += 1
        if self.calls >= self.stop_after:
            raise SystemExit(0)

    def next_deadline(self):
        return None


def test_a_notification_beats_the_periodic_floor(setup, monkeypatch):  # noqa: F811
    service, _, _ = setup
    service.settings.resync_seconds = 3600
    service.settings.debounce_seconds = 0
    wake = threading.Event()
    recorder = Recorder(service.state, stop_after=2)
    monkeypatch.setattr(loop, "probe_discovery", lambda settings: True)
    monkeypatch.setattr(loop, "await_discovery", lambda settings: True)

    started = time.monotonic()
    threading.Timer(0.05, wake.set).start()
    with pytest.raises(SystemExit):
        loop.run_forever(recorder, service.settings, wake)

    assert recorder.calls == 2
    assert time.monotonic() - started < 5  # not the 3600s floor


def test_a_burst_of_notifications_collapses_into_one_reconcile(
    setup, monkeypatch
):  # noqa: F811
    """One request per resource written, so a startup sync is a burst."""
    service, _, _ = setup
    service.settings.resync_seconds = 3600
    service.settings.debounce_seconds = 0.2
    wake = threading.Event()
    recorder = Recorder(service.state, stop_after=2)
    monkeypatch.setattr(loop, "probe_discovery", lambda settings: True)
    monkeypatch.setattr(loop, "await_discovery", lambda settings: True)

    def burst():
        for _ in range(5):
            wake.set()
            time.sleep(0.02)

    threading.Timer(0.05, burst).start()
    with pytest.raises(SystemExit):
        loop.run_forever(recorder, service.settings, wake)

    # One before the burst, one after it settles. Not five.
    assert recorder.calls == 2


def test_the_loop_keeps_going_after_a_failed_reconcile(
    setup, monkeypatch
):  # noqa: F811
    service, _, _ = setup
    service.settings.resync_seconds = 0.01
    service.settings.debounce_seconds = 0
    monkeypatch.setattr(loop, "probe_discovery", lambda settings: True)
    monkeypatch.setattr(loop, "await_discovery", lambda settings: True)

    class Exploding(Recorder):
        def reconcile_once(self):
            self.calls += 1
            if self.calls >= self.stop_after:
                raise SystemExit(0)
            raise RuntimeError("kubernetes said no")

    with pytest.raises(SystemExit):
        loop.run_forever(
            Exploding(service.state, 3), service.settings, threading.Event()
        )


def test_discovery_is_re_probed_until_it_reports_a_sync(
    setup, monkeypatch
):  # noqa: F811
    """A late sidecar must not leave the loop refusing forever."""
    service, _, _ = setup
    service.settings.resync_seconds = 0.01
    service.settings.debounce_seconds = 0
    probes = []
    monkeypatch.setattr(loop, "await_discovery", lambda settings: False)
    monkeypatch.setattr(
        loop,
        "probe_discovery",
        lambda settings: bool(probes.append(1)) or len(probes) > 2,
    )

    recorder = Recorder(service.state, stop_after=5)
    with pytest.raises(SystemExit):
        loop.run_forever(recorder, service.settings, threading.Event())

    assert service.state.discovery_synced is True
    assert len(probes) == 3  # stops once it succeeds
