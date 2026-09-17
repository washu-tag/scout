"""A doorbell on the fragment ConfigMaps. Never a source of truth.

It may ring the bell and, for a deletion it actually witnessed, name the
fragment that went away -- nothing more. Every value the reconciler acts on
comes from a LIST or GET it does itself, and absence is never concluded here: an
empty watch cache does not distinguish "no fragments exist" from "not synced
yet", and the second read literally would delete every fragment client.

So a missed event, a dropped connection or a stale resourceVersion cost latency
and nothing else, which is what keeps this short of an informer.
"""

import json
import logging
import threading
import urllib.parse
from collections.abc import Callable, Iterator

from . import shutdown
from .k8s import ApiError, Client

log = logging.getLogger("keycloak-fragment-reconciler")

BACKOFF_SECONDS = 5.0
# A permanent failure -- RBAC not applied yet, a bad token -- should not retry
# at the same rate forever.
MAX_BACKOFF_SECONDS = 300.0
# Ask the server to close the watch periodically, and give up a little after it
# should have. Without both, a half-open connection is a doorbell that has
# silently stopped ringing until the kernel notices, which is hours.
SERVER_TIMEOUT_SECONDS = 300
# How long past the server's own deadline to keep waiting, so a close that is
# merely slow does not read as a dropped connection.
READ_TIMEOUT_MARGIN_SECONDS = 30
READ_TIMEOUT_SECONDS = SERVER_TIMEOUT_SECONDS + READ_TIMEOUT_MARGIN_SECONDS

# Called with (namespace, name) for a deletion this watch saw with its own eyes.
Witness = Callable[[str, str], None]


def _events(lines: Iterator[str]) -> Iterator[dict]:
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except ValueError:
            log.debug("ignoring an unparsable line from the watch")


def watch_once(
    client: Client,
    label_selector: str,
    wake: threading.Event,
    witness: Witness,
    version: str = "",
) -> str:
    """One connection's worth of events; the resourceVersion to resume from.

    Always watches *from* a version, taking one from a fresh list when the
    caller has none. Opening a watch without one replays the collection as
    synthetic ADDED events, and nothing distinguishes those from a genuine
    creation -- so suppressing ADDED to compensate would silently eat the one
    event this watch exists to deliver.
    """
    if not version:
        version = client.collection_version(label_selector)
    params = {
        "labelSelector": label_selector,
        "watch": "1",
        "allowWatchBookmarks": "true",
        "timeoutSeconds": str(SERVER_TIMEOUT_SECONDS),
        "resourceVersion": version,
    }
    path = f"/api/v1/configmaps?{urllib.parse.urlencode(params)}"
    with client.stream(path, read_timeout=READ_TIMEOUT_SECONDS) as lines:
        for event in _events(lines):
            if shutdown.requested.is_set():
                break
            obj = event.get("object") or {}
            meta = obj.get("metadata") or {}
            version = meta.get("resourceVersion") or version
            kind = event.get("type")
            if kind == "ERROR":
                raise ApiError(
                    int(obj.get("code") or 0), str(obj.get("message") or "watch error")
                )
            if kind == "BOOKMARK":
                # Carries a resourceVersion and nothing else, so a reconnect can
                # resume rather than start over.
                continue
            namespace, name = meta.get("namespace"), meta.get("name")
            if not (namespace and name):
                continue
            log.info(
                "fragment %s/%s %s; waking the reconciler",
                namespace,
                name,
                (kind or "changed").lower(),
            )
            if kind == "DELETED":
                witness(namespace, name)
            wake.set()
    return version


def run_forever(
    client: Client,
    label_selector: str,
    wake: threading.Event,
    witness: Witness,
    *,
    backoff: float = BACKOFF_SECONDS,
) -> None:
    version = ""
    wait = backoff
    try:
        while not shutdown.requested.is_set():
            try:
                version = watch_once(client, label_selector, wake, witness, version)
                wait = backoff
            except Exception as exc:
                if shutdown.requested.is_set():
                    break
                # Which failure it was does not change what to do about it, and
                # a stale resourceVersion is the one a retry cannot carry
                # forward.
                log.warning("fragment watch dropped (%s); restarting in %ss", exc, wait)
                version = ""
                shutdown.sleep(wait)
                wait = min(wait * 2, MAX_BACKOFF_SECONDS)
                continue
            shutdown.sleep(backoff)
    finally:
        if shutdown.requested.is_set():
            log.info("the fragment watch stopped with the process")
        else:
            # Nothing else should end this thread. If something does, the
            # reconciler is left with only its periodic resync -- and with
            # `resync_seconds: -1` there is no periodic resync, so nothing will
            # wake it at all. Either way this line is the only clue.
            log.error(
                "the fragment watch has stopped; changes now wait out the "
                "periodic resync, or are not noticed at all if it is disabled"
            )
