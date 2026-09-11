"""Stopping.

Installing a SIGTERM handler gives up the default disposition, which is to end
the process at once. So the value of these is not that a stop is graceful --
it is that every wait the reconciler can be parked in ends on the same event,
because any one that does not is a restart that sits out a timeout instead.
"""

import threading
import time
from pathlib import Path

import pytest
from conftest import fragment_yaml, setup, write_fragment  # noqa: F401

from scout_app_manager import loop, shutdown
from scout_app_manager.apply import RealmApplier
from scout_app_manager.settings import Settings


@pytest.fixture(autouse=True)
def clean():
    """`requested` is process-wide, so no test may leak it into the next."""
    shutdown.requested.clear()
    yield
    shutdown.requested.clear()


def test_sleep_returns_early_and_says_so():
    started = time.monotonic()
    threading.Timer(0.05, shutdown.requested.set).start()

    assert shutdown.sleep(30) is False
    assert time.monotonic() - started < 5


def test_sleep_reports_a_full_wait_as_carry_on():
    assert shutdown.sleep(0.01) is True


def test_the_reconcile_loop_stops_between_passes(setup, monkeypatch):  # noqa: F811
    """Between passes, not during one: a half-applied realm has no meaning."""
    service, _, client = setup
    service.settings.resync_seconds = 3600
    monkeypatch.setattr(loop, "await_discovery", lambda settings: True)
    monkeypatch.setattr(loop, "probe_discovery", lambda settings: True)
    wake = threading.Event()

    threading.Timer(0.05, lambda: (shutdown.requested.set(), wake.set())).start()
    started = time.monotonic()
    loop.run_forever(service, service.settings, wake)

    # Returned rather than sitting out the 3600s floor, and the pass it was in
    # finished.
    assert time.monotonic() - started < 5
    assert len(client.created_jobs) == 1


def test_waiting_on_discovery_stops_too(monkeypatch):
    """90s of it by default, and it is the first thing the process does."""
    settings = Settings(discovery_health_url="http://127.0.0.1:1/healthz")
    monkeypatch.setattr(loop, "probe_discovery", lambda settings: False)
    threading.Timer(0.05, shutdown.requested.set).start()

    started = time.monotonic()
    assert loop.await_discovery(settings) is False
    assert time.monotonic() - started < 5


def test_an_apply_in_flight_is_abandoned_not_waited_out(setup):  # noqa: F811
    """The Job is a pod of its own; the next process finds it by name.

    Holding this one open for a five-minute import would only run down the
    termination grace and end in SIGKILL.
    """
    service, _, client = setup
    applier = RealmApplier(service.settings, client, "scout-core")
    client.jobs["app-manager-apply-pending"] = {}
    client.job_status["app-manager-apply-pending"] = {}
    threading.Timer(0.05, shutdown.requested.set).start()

    started = time.monotonic()
    ok, detail = applier._wait("app-manager-apply-pending")

    assert ok is False
    assert "still running when this process stopped" in detail
    assert time.monotonic() - started < 5


def test_nothing_waits_outside_the_stop_event():
    """The invariant the rest of this file is only samples of.

    A `time.sleep` anywhere on a path a signal can reach is that many seconds
    a restart sits out, and it is one line to reintroduce.
    """
    package = Path(shutdown.__file__).parent
    offenders = [
        module.name
        for module in sorted(package.glob("*.py"))
        if module.name != "shutdown.py"
        and "time.sleep" in module.read_text(encoding="utf-8")
    ]

    assert offenders == []
