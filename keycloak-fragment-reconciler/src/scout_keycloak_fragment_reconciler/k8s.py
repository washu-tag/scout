"""Just enough Kubernetes API for the reconciler.

Deliberately not the official client. This needs four verbs on three resource
kinds, and hand-rolling them keeps the permission surface legible: every call
the service can make is a function in this file, next to the RBAC that grants
it.

Two absences are deliberate. There is no ConfigMap write, because fragments
arrive on Flux- or Helm-managed objects and a status write starts a revert loop.
There is no Secret `list` or `watch`, because a credential is fetched by name
under a `resourceNames`-scoped grant from the app's own chart -- the tightest
thing RBAC offers, at the cost of noticing a rotation on the next resync.
"""

import base64
import binascii
import logging
import os
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx2 as httpx

log = logging.getLogger("keycloak-fragment-reconciler")

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
TOKEN_PATH = SA_DIR / "token"
CA_PATH = SA_DIR / "ca.crt"
NAMESPACE_PATH = SA_DIR / "namespace"

# Bounds an ordinary request end to end. Too short for a watch, which passes
# its own read timeout and takes STREAM_SETUP_TIMEOUT_SECONDS for the rest.
REQUEST_TIMEOUT_SECONDS = 30.0
# Every phase of a streaming request except the read: connecting, sending, and
# waiting for a pooled connection are all quick or broken.
STREAM_SETUP_TIMEOUT_SECONDS = 10.0
# How much of an error body to carry into the exception message.
ERROR_EXCERPT_CHARS = 500
# Matches the Events API's own limit on a message, so reporting an outcome
# cannot fail validation on the length of a detail string.
MAX_EVENT_MESSAGE_CHARS = 1024


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"kubernetes API {status}: {message}")
        self.status = status


class TransportError(ApiError):
    """The request never reached an answer: no token to send, DNS, connect,
    TLS, or a timeout.

    Status 0 collides with no HTTP status, so `exc.status == 404` still means
    exactly a 404. The distinction matters because GC may only infer an orphan
    from a LIST that completed, and this is the case where an empty result means
    "we could not ask".
    """

    def __init__(self, message: str):
        RuntimeError.__init__(self, f"kubernetes API unreachable: {message}")
        self.status = 0


def _decoded(response: httpx.Response, context: str) -> dict:
    """The body as JSON, with a decode failure classified like any other.

    A `ValueError` is what an undecodable body raises, and no handler between
    here and the run loop narrows to it, so unclassified it would end the pass
    rather than the one fragment. It carries the answer's own status rather
    than 0, because an answer did arrive: a garbled LIST is a read that failed,
    not a read that could not be made.
    """
    try:
        return response.json()
    except ValueError as exc:
        raise ApiError(
            response.status_code, f"{context}: body is not JSON: {exc}"
        ) from None


def value_of(secret: dict | None, key: str) -> tuple[str | None, str]:
    """One key out of a fetched Secret, or why there is no value to use.

    Every way this fails fails closed, and each says which way it was: the
    reason reaches the fragment's author, and the several causes want different
    things fixed. Reported rather than raised, because raising would reach the
    reconcile loop and stall every other fragment.
    """
    if not secret:
        return None, "no such Secret"
    data = secret.get("data") or {}
    encoded = data.get(key)
    if encoded is None:
        present = ", ".join(sorted(data)) or "no keys"
        return None, f"the Secret has no such key; it has {present}"
    try:
        value = base64.b64decode(encoded).decode("utf-8")
    except (UnicodeDecodeError, binascii.Error, ValueError):
        return None, "the value is not UTF-8 text"
    if not value:
        return None, "the value is empty"
    return value, ""


class Client:
    def __init__(self, timeout: float = REQUEST_TIMEOUT_SECONDS):
        host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        self.base = f"https://{host}:{port}"
        verify = str(CA_PATH) if CA_PATH.exists() else True
        self._http = httpx.Client(verify=verify, timeout=timeout)

    def namespace(self) -> str:
        try:
            return NAMESPACE_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            return os.environ.get("POD_NAMESPACE", "default")

    def _headers(self) -> dict[str, str]:
        # Projected service-account tokens rotate; read on every call rather
        # than caching one that expires mid-session. An unreadable file is
        # classified rather than raised as an `OSError`, which is no
        # `httpx.HTTPError` and would escape every handler downstream.
        try:
            token = TOKEN_PATH.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise TransportError(
                f"the service-account token at {TOKEN_PATH} is unreadable: {exc}"
            ) from None
        return {"Authorization": f"Bearer {token}"}

    def request(self, method: str, path: str, *, json: object = None) -> dict:
        try:
            response = self._http.request(
                method, f"{self.base}{path}", json=json, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise TransportError(f"{method} {path}: {exc}") from exc
        if response.status_code >= 400:
            raise ApiError(response.status_code, response.text[:ERROR_EXCERPT_CHARS])
        if not response.content:
            return {}
        return _decoded(response, f"{method} {path}")

    def _get(self, path: str) -> dict | None:
        """A read where absence is an answer, not an error."""
        try:
            return self.request("GET", path)
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise

    @contextmanager
    def stream(
        self, path: str, *, read_timeout: float | None = None
    ) -> Iterator[Iterator[str]]:
        """A long-lived streaming GET, for the watch.

        Takes its own read timeout because the client's would abort an idle
        watch. Callers should still pass one: with none, a half-open connection
        blocks until the kernel gives up, which is hours.
        """
        timeout = httpx.Timeout(
            connect=STREAM_SETUP_TIMEOUT_SECONDS,
            read=read_timeout,
            write=STREAM_SETUP_TIMEOUT_SECONDS,
            pool=STREAM_SETUP_TIMEOUT_SECONDS,
        )
        try:
            with self._http.stream(
                "GET", f"{self.base}{path}", headers=self._headers(), timeout=timeout
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    raise ApiError(
                        response.status_code, response.text[:ERROR_EXCERPT_CHARS]
                    )
                yield response.iter_lines()
        except httpx.HTTPError as exc:
            raise TransportError(f"GET {path}: {exc}") from exc

    # --- ConfigMaps (read only) -----------------------------------------

    def list_configmaps(self, label_selector: str) -> list[dict]:
        """Every fragment ConfigMap in the cluster, by label.

        Cluster-wide because RBAC cannot express a label selector; the label and
        any namespace allowlist narrow what is *read*, never what is granted.

        The authoritative read. A failure raises, and GC treats that as "skip
        this cycle" rather than "no fragments exist".
        """
        query = urllib.parse.urlencode({"labelSelector": label_selector})
        result = self.request("GET", f"/api/v1/configmaps?{query}")
        return result.get("items", [])

    def collection_version(self, label_selector: str) -> str:
        """The resourceVersion to start a watch from.

        A watch opened without one replays the collection as synthetic ADDED
        events, and nothing in the stream distinguishes those from real
        creations. `limit=1` because only the version is wanted.
        """
        query = urllib.parse.urlencode({"labelSelector": label_selector, "limit": "1"})
        result = self.request("GET", f"/api/v1/configmaps?{query}")
        return (result.get("metadata") or {}).get("resourceVersion", "")

    # --- Secrets (read one, by name, never listed) -----------------------

    def get_secret(self, namespace: str, name: str) -> dict | None:
        return self._get(f"/api/v1/namespaces/{namespace}/secrets/{name}")

    # --- Events ----------------------------------------------------------

    def emit_event(
        self,
        *,
        involved: dict,
        reason: str,
        message: str,
        event_type: str = "Normal",
        component: str = "keycloak-fragment-reconciler",
        timestamp: str,
    ) -> bool:
        """Report one fragment's outcome against the ConfigMap it came from.

        Best-effort: failing to report must never fail the reconcile it was
        reporting on. Answers whether the report landed, because an Event is
        the only reporting channel there is and a caller that reports on change
        only must not remember a lost one as reported.
        """
        meta = involved.get("metadata") or {}
        namespace = meta.get("namespace") or self.namespace()
        body = {
            "apiVersion": "v1",
            "kind": "Event",
            "metadata": {
                "generateName": f"{meta.get('name', 'fragment')}.",
                "namespace": namespace,
            },
            "involvedObject": {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "name": meta.get("name"),
                "namespace": namespace,
                "uid": meta.get("uid"),
                "resourceVersion": meta.get("resourceVersion"),
            },
            "reason": reason,
            "message": message[:MAX_EVENT_MESSAGE_CHARS],
            "type": event_type,
            "source": {"component": component},
            "firstTimestamp": timestamp,
            "lastTimestamp": timestamp,
            "eventTime": None,
            "count": 1,
        }
        try:
            self.request("POST", f"/api/v1/namespaces/{namespace}/events", json=body)
        except ApiError as exc:
            log.warning(
                "could not report %s on %s/%s: %s",
                reason,
                namespace,
                meta.get("name"),
                exc,
            )
            return False
        return True
