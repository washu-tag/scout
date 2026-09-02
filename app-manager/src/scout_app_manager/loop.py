"""Scheduling for the reconcile: woken by discovery, floored by a timer."""

import logging
import threading
import time

import httpx2 as httpx

from .service import AppManagerService
from .settings import Settings

log = logging.getLogger("app-manager")


def probe_discovery(settings: Settings) -> bool:
    """One non-blocking ask: has the sidecar finished its initial sync?"""
    if not settings.discovery_health_url:
        return True
    try:
        return httpx.get(settings.discovery_health_url, timeout=5.0).status_code == 200
    except httpx.HTTPError:
        return False


def await_discovery(settings: Settings) -> bool:
    """Block until the sidecar has written what it found.

    The sidecar's request-on-change cannot answer this: with no matching
    ConfigMaps it writes nothing and sends nothing, so "no notification yet" and
    "synced, found nothing" look identical. Only the health endpoint separates
    them. False holds every retraction rather than applying a base-only realm.
    """
    if not settings.discovery_health_url:
        return True
    deadline = time.monotonic() + settings.discovery_wait_seconds
    while time.monotonic() < deadline:
        if probe_discovery(settings):
            log.info("discovery sidecar reports its initial sync is complete")
            return True
        time.sleep(1)
    log.error(
        "discovery sidecar was not ready within %ss; reconciling, but nothing "
        "will be retracted until it reports a complete sync",
        settings.discovery_wait_seconds,
    )
    return False


# Unsynced discovery is checked more often than the floor, but not so often that
# a broken sidecar means a reconcile every few seconds.
DEGRADED_INTERVAL = 30.0
MINIMUM_WAIT = 5.0


def next_wait(service: AppManagerService, settings: Settings) -> float:
    """How long to sleep if no notification arrives."""
    if not service.state.discovery_synced:
        return min(settings.resync_seconds, DEGRADED_INTERVAL)
    deadline = service.next_deadline()
    if deadline is None:
        return settings.resync_seconds
    # +1 so the grace has definitely elapsed by the time we look.
    return min(settings.resync_seconds, max(deadline + 1, MINIMUM_WAIT))


def run_forever(
    service: AppManagerService, settings: Settings, wake: threading.Event
) -> None:
    service.state.discovery_synced = await_discovery(settings)
    while True:
        if not service.state.discovery_synced:
            service.state.discovery_synced = probe_discovery(settings)
        try:
            service.reconcile_once()
        except Exception:
            log.exception("reconcile failed; last applied realm stays in place")
        # One request per resource written, so let the burst land before acting.
        if wake.wait(next_wait(service, settings)):
            wake.clear()
            time.sleep(settings.debounce_seconds)
            wake.clear()
