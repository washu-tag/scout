"""A doorbell on the objects the reconcile reads.

Never a source of truth. A watch only sets `wake`; every value the reconciler
acts on still comes from the GET it does per reconcile, by name. So a missed
event, a server-closed connection, an expired token or a resourceVersion too
old all cost latency and nothing else -- the resync floor is the backstop. That
is what keeps this a few dozen lines rather than a watch client with replay
semantics, and it is why the watch is allowed to be this careless.

One collection per kind, in the reconciler's own namespace, filtered
client-side against the names the last reconcile asked for. Nothing is selected
by label: a fragment needs no label on its credential because its `secretRef`
already names it, and the base realm is read from the ConfigMap the chart
names.
"""

import json
import logging
import threading
from collections.abc import Callable, Iterator

from . import shutdown
from .k8s import ApiError, Client

log = logging.getLogger("app-manager")

BACKOFF_SECONDS = 5.0
# A permanent failure -- RBAC not applied yet, a bad token -- should not retry
# at the same rate forever.
MAX_BACKOFF_SECONDS = 300.0
# Ask the server to close the watch periodically, and give up a little after it
# should have. Without both, a half-open connection is a doorbell that has
# silently stopped ringing until the kernel notices, which is hours.
SERVER_TIMEOUT_SECONDS = 300
READ_TIMEOUT_SECONDS = SERVER_TIMEOUT_SECONDS + 30


def _events(lines: Iterator[str]) -> Iterator[dict]:
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except ValueError:
            log.debug("ignoring an unparsable line from a watch")


def watch_once(
    client: Client,
    namespace: str,
    resource: str,
    names: Callable[[], set[str]],
    wake: threading.Event,
    version: str = "",
) -> str:
    """One connection's worth of events; the resourceVersion to resume from.

    Resuming is opportunistic. It saves the server replaying the whole
    collection on each reconnect, and if the version is ever too old the server
    says so in-band and the caller starts over.
    """
    # Without a resourceVersion the server replays the collection as synthetic
    # ADDED before streaming changes. Waking on those would turn a flapping
    # connection into a full reconcile every few seconds, so a fresh
    # connection ignores them; a real creation costs one resync floor.
    replaying = not version
    query = f"watch=1&allowWatchBookmarks=true&timeoutSeconds={SERVER_TIMEOUT_SECONDS}"
    if version:
        query += f"&resourceVersion={version}"
    with client.stream(
        f"/api/v1/namespaces/{namespace}/{resource}?{query}",
        read_timeout=READ_TIMEOUT_SECONDS,
    ) as lines:
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
                continue
            if kind != "ADDED":
                replaying = False
            elif replaying:
                continue
            name = meta.get("name")
            if name and name in names():
                log.info(
                    "%s %s/%s %s; waking the reconciler",
                    resource[:-1],
                    namespace,
                    name,
                    (kind or "changed").lower(),
                )
                wake.set()
    return version


def run_forever(
    client: Client,
    namespace: str,
    resource: str,
    names: Callable[[], set[str]],
    wake: threading.Event,
    *,
    backoff: float = BACKOFF_SECONDS,
) -> None:
    version = ""
    wait = backoff
    try:
        while not shutdown.requested.is_set():
            try:
                version = watch_once(client, namespace, resource, names, wake, version)
                wait = backoff
            except Exception as exc:
                if shutdown.requested.is_set():
                    break
                # Which failure it was does not change what to do about it, and
                # a stale resourceVersion is the one a retry cannot carry
                # forward.
                log.warning(
                    "%s watch dropped (%s); restarting in %ss", resource, exc, wait
                )
                version = ""
                shutdown.sleep(wait)
                wait = min(wait * 2, MAX_BACKOFF_SECONDS)
                continue
            shutdown.sleep(backoff)
    finally:
        if shutdown.requested.is_set():
            log.info("the %s watch stopped with the process", resource)
        else:
            # Nothing else should end this thread. If something does, the
            # reconciler keeps working off the resync floor and the only clue
            # is here.
            log.error(
                "the %s watch has stopped; changes now wait out the resync floor",
                resource,
            )
