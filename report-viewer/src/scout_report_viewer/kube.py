"""The two Kubernetes API reads action discovery needs (ADR 0039).

Hand-rolled rather than the official client, like the Keycloak fragment
reconciler's: listing labeled ConfigMaps and getting one Secret by name are
the only calls, and each sits next to the RBAC that grants it
(`helm/report-viewer/templates/rbac.yaml` for the list, the publishing app's
own `resourceNames`-scoped Role for the Secret).
"""

from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
REQUEST_TIMEOUT_SECONDS = 10.0


class KubeError(RuntimeError):
    pass


def _base_url() -> str:
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if ":" in host:
        host = f"[{host}]"
    return f"https://{host}:{port}"


async def _get(path: str) -> dict:
    # Projected tokens rotate, so the file is read on every call.
    try:
        token = (SA_DIR / "token").read_text().strip()
    except OSError as exc:
        raise KubeError(f"service-account token unreadable: {exc}") from None
    ca = SA_DIR / "ca.crt"
    try:
        async with httpx.AsyncClient(
            base_url=_base_url(),
            verify=str(ca) if ca.is_file() else True,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as client:
            response = await client.get(
                path, headers={"Authorization": f"Bearer {token}"}
            )
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise KubeError(f"GET {path}: {exc}") from None
    if response.status_code != 200:
        raise KubeError(f"GET {path}: HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise KubeError(f"GET {path}: body is not JSON") from None


async def list_configmaps(label_selector: str, namespace: str | None) -> list[dict]:
    """Labeled ConfigMaps in `namespace`, or in every namespace when None."""
    query = urlencode({"labelSelector": label_selector})
    scope = f"/namespaces/{quote(namespace, safe='')}" if namespace else ""
    result = await _get(f"/api/v1{scope}/configmaps?{query}")
    return result.get("items") or []


async def secret_value(namespace: str, name: str, key: str) -> str:
    """One key of one Secret, decoded and stripped."""
    secret = await _get(
        f"/api/v1/namespaces/{quote(namespace, safe='')}"
        f"/secrets/{quote(name, safe='')}"
    )
    encoded = (secret.get("data") or {}).get(key)
    if encoded is None:
        raise KubeError(f"Secret {namespace}/{name} has no key {key!r}")
    try:
        value = base64.b64decode(encoded).decode("utf-8").strip()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise KubeError(f"Secret {namespace}/{name} key {key!r} is not text") from None
    if not value:
        raise KubeError(f"Secret {namespace}/{name} key {key!r} is empty")
    return value
