"""Just enough Kubernetes API for the reconciler.

Deliberately not the official client: the reconciler needs five verbs on three
resource kinds, and a hand-rolled client keeps the vendored wheel set small
enough to build in an air-gapped cluster. It also keeps the permission surface
obvious -- every call the reconciler can make is a function in this file.
"""

import base64
import binascii
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx2 as httpx

log = logging.getLogger("app-manager")

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
TOKEN_PATH = SA_DIR / "token"
CA_PATH = SA_DIR / "ca.crt"
NAMESPACE_PATH = SA_DIR / "namespace"


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"kubernetes API {status}: {message}")
        self.status = status


def value_of(secret: dict | None, key: str) -> str | None:
    """One key out of a fetched Secret. A function, so a caller can read
    several keys and the resourceVersion from one GET.

    A Secret holds bytes, and nothing stops a key of one the reconciler reads
    from holding something that is not text. Unreadable is reported as absent,
    which fails the value closed -- the alternative is an exception out of a
    pure accessor, which reaches the reconcile loop and stops every realm
    update until someone finds the Secret.
    """
    if not secret:
        return None
    encoded = (secret.get("data") or {}).get(key)
    if encoded is None:
        return None
    try:
        return base64.b64decode(encoded).decode("utf-8")
    except (UnicodeDecodeError, binascii.Error, ValueError):
        name = (secret.get("metadata") or {}).get("name", "(unnamed)")
        log.warning(
            "secret %s key %s is not UTF-8 text; treating it as absent", name, key
        )
        return None


def version_of(secret: dict | None) -> str:
    """The Secret's resourceVersion, which moves when and only when its
    contents do. How a credential rotation is noticed now that rotating one
    leaves the realm document untouched."""
    if not secret:
        return ""
    return (secret.get("metadata") or {}).get("resourceVersion") or ""


class Client:
    def __init__(self, timeout: float = 30.0):
        host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        self.base = f"https://{host}:{port}"
        verify = str(CA_PATH) if CA_PATH.exists() else True
        self._http = httpx.Client(verify=verify, timeout=timeout)

    def namespace(self) -> str:
        try:
            return NAMESPACE_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            return os.environ.get("APP_MANAGER_NAMESPACE", "default")

    def _headers(self) -> dict[str, str]:
        # Projected service-account tokens rotate; read on every call rather
        # than caching a token that expires mid-session.
        token = TOKEN_PATH.read_text(encoding="utf-8").strip()
        return {"Authorization": f"Bearer {token}"}

    def request(
        self,
        method: str,
        path: str,
        *,
        json: object = None,
        headers: dict | None = None,
    ) -> dict:
        merged = self._headers()
        if headers:
            merged.update(headers)
        response = self._http.request(
            method, f"{self.base}{path}", json=json, headers=merged
        )
        if response.status_code >= 400:
            raise ApiError(response.status_code, response.text[:500])
        if not response.content:
            return {}
        return response.json()

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
        """A long-lived streaming GET, for a watch.

        Its own timeout: this client is built with a 30s read timeout, which
        would abort an idle watch every 30 seconds. A caller should still pass
        one -- without any read timeout a half-open connection blocks until the
        kernel gives up on it, which is hours.
        """
        timeout = httpx.Timeout(connect=10.0, read=read_timeout, write=10.0, pool=10.0)
        with self._http.stream(
            "GET", f"{self.base}{path}", headers=self._headers(), timeout=timeout
        ) as response:
            if response.status_code >= 400:
                response.read()
                raise ApiError(response.status_code, response.text[:500])
            yield response.iter_lines()

    # --- ConfigMaps -----------------------------------------------------

    def get_configmap(self, namespace: str, name: str) -> dict | None:
        return self._get(f"/api/v1/namespaces/{namespace}/configmaps/{name}")

    def put_configmap_data(
        self,
        namespace: str,
        name: str,
        data: dict[str, str],
        labels: dict[str, str] | None = None,
    ) -> None:
        """Merge-patch the ConfigMap's data, creating it the first time."""
        metadata: dict = {"name": name, "namespace": namespace}
        if labels:
            metadata["labels"] = labels
        collection = f"/api/v1/namespaces/{namespace}/configmaps"
        try:
            self.request(
                "PATCH",
                f"{collection}/{name}",
                json={"metadata": metadata, "data": data},
                headers={"Content-Type": "application/merge-patch+json"},
            )
        except ApiError as exc:
            if exc.status != 404:
                raise
            self.request(
                "POST",
                collection,
                json={
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": metadata,
                    "data": data,
                },
            )

    # --- Secrets --------------------------------------------------------

    # Read only. The reconciler used to write one -- the composed realm -- and
    # that is a ConfigMap now, so nothing it does needs a Secret write.

    def get_secret(self, namespace: str, name: str) -> dict | None:
        return self._get(f"/api/v1/namespaces/{namespace}/secrets/{name}")

    # --- Jobs -----------------------------------------------------------

    def get_job(self, namespace: str, name: str) -> dict | None:
        return self._get(f"/apis/batch/v1/namespaces/{namespace}/jobs/{name}")

    def create_job(self, namespace: str, body: dict) -> dict:
        return self.request(
            "POST", f"/apis/batch/v1/namespaces/{namespace}/jobs", json=body
        )

    def delete_job(self, namespace: str, name: str) -> None:
        try:
            self.request(
                "DELETE",
                f"/apis/batch/v1/namespaces/{namespace}/jobs/{name}"
                "?propagationPolicy=Background",
            )
        except ApiError as exc:
            if exc.status != 404:
                raise

    def list_jobs(self, namespace: str, label_selector: str) -> list[dict]:
        result = self.request(
            "GET",
            f"/apis/batch/v1/namespaces/{namespace}/jobs?labelSelector={label_selector}",
        )
        return result.get("items", [])

    def job_logs(self, namespace: str, job_name: str, tail: int = 40) -> str:
        pods = self.request(
            "GET",
            f"/api/v1/namespaces/{namespace}/pods?labelSelector=job-name%3D{job_name}",
        ).get("items", [])
        chunks = []
        for pod in pods:
            name = pod["metadata"]["name"]
            try:
                token = self._headers()
                response = self._http.get(
                    f"{self.base}/api/v1/namespaces/{namespace}/pods/{name}/log"
                    f"?tailLines={tail}",
                    headers=token,
                )
                chunks.append(response.text)
            except httpx.HTTPError as exc:  # pragma: no cover - diagnostics only
                chunks.append(f"(could not read logs for {name}: {exc})")
        return "\n".join(chunks)
