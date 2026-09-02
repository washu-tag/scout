"""Just enough Kubernetes API for the reconciler.

Deliberately not the official client: the reconciler needs five verbs on three
resource kinds, and a hand-rolled client keeps the vendored wheel set small
enough to build in an air-gapped cluster. It also keeps the permission surface
obvious -- every call the reconciler can make is a function in this file.
"""

import base64
import os
from pathlib import Path

import httpx2 as httpx

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
TOKEN_PATH = SA_DIR / "token"
CA_PATH = SA_DIR / "ca.crt"
NAMESPACE_PATH = SA_DIR / "namespace"


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"kubernetes API {status}: {message}")
        self.status = status


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

    def _put(
        self,
        resource: str,
        kind: str,
        namespace: str,
        name: str,
        data: dict[str, str],
        labels: dict[str, str] | None,
        extra: dict | None = None,
    ) -> None:
        """Merge-patch the object's data, creating it the first time."""
        metadata: dict = {"name": name, "namespace": namespace}
        if labels:
            metadata["labels"] = labels
        collection = f"/api/v1/namespaces/{namespace}/{resource}"
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
                    "kind": kind,
                    "metadata": metadata,
                    "data": data,
                    **(extra or {}),
                },
            )

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
        self._put("configmaps", "ConfigMap", namespace, name, data, labels)

    # --- Secrets --------------------------------------------------------

    def get_secret(self, namespace: str, name: str) -> dict | None:
        return self._get(f"/api/v1/namespaces/{namespace}/secrets/{name}")

    def get_secret_value(self, namespace: str, name: str, key: str) -> str | None:
        secret = self.get_secret(namespace, name)
        if not secret:
            return None
        encoded = (secret.get("data") or {}).get(key)
        if encoded is None:
            return None
        return base64.b64decode(encoded).decode("utf-8")

    def put_secret(
        self,
        namespace: str,
        name: str,
        values: dict[str, str],
        labels: dict[str, str] | None = None,
    ) -> None:
        data = {
            k: base64.b64encode(v.encode("utf-8")).decode("ascii")
            for k, v in values.items()
        }
        self._put(
            "secrets", "Secret", namespace, name, data, labels, {"type": "Opaque"}
        )

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
