"""Scout helper for Trino queries with transparent token refresh.

Notebooks `from scout_trino import query`; the helper takes care of all
authentication. Access tokens are refreshed via the JupyterHub API
(which holds the Keycloak `refresh_token` server-side), so user sessions
keep working indefinitely without the notebook seeing any token state.

The credential discipline:
  - The user's `refresh_token` and Keycloak `client_secret` stay in the
    JupyterHub Hub process — they're never in the notebook pod.
  - The notebook pod has only its `JUPYTERHUB_API_TOKEN` (per-user
    scoped) and short-lived access tokens fetched from the Hub.
  - When this module asks the Hub for the user's `auth_state`, the
    Hub's `OAuthenticator.refresh_user` middleware runs first — it
    refreshes the access token if it's near expiry, then returns the
    fresh value. Asking for the token therefore refreshes it.

If `refresh_user` fails (e.g. the Keycloak SSO Session Idle TTL has
elapsed, or an admin disabled the user), the helper raises a
`RuntimeError` with instructions to log back in to JupyterHub.
"""

import base64
import json
import os
import threading
import time

import pandas as pd
import requests
from trino.auth import JWTAuthentication
from trino.dbapi import connect

_REFRESH_BUFFER_SECONDS = 60


class _TokenManager:
    """Caches the user's Keycloak access token; refreshes via the Hub."""

    def __init__(self):
        self._lock = threading.Lock()
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self._hub_url = os.environ["JUPYTERHUB_API_URL"].rstrip("/")
        self._hub_token = os.environ["JUPYTERHUB_API_TOKEN"]
        self._user = os.environ["JUPYTERHUB_USER"]

    def get(self) -> str:
        with self._lock:
            if (
                self._access_token
                and time.time() < self._expires_at - _REFRESH_BUFFER_SECONDS
            ):
                return self._access_token
            return self._fetch_from_hub()

    def _fetch_from_hub(self) -> str:
        response = requests.get(
            f"{self._hub_url}/users/{self._user}",
            headers={"Authorization": f"token {self._hub_token}"},
            params={"include_auth_state": "true"},
            timeout=10,
        )
        if response.status_code == 403:
            raise RuntimeError(
                "JupyterHub denied access to this user's auth_state. "
                "The singleuser role is missing the `admin:auth_state!user` "
                "scope — see the jupyter Ansible role's "
                "`Spawner.server_token_scopes` config."
            )
        response.raise_for_status()
        auth_state = (response.json() or {}).get("auth_state") or {}
        access_token = auth_state.get("access_token")
        if not access_token:
            raise RuntimeError(
                "Keycloak session has expired. Log out of JupyterHub "
                "and log back in to continue querying."
            )
        self._access_token = access_token
        self._expires_at = _jwt_exp(access_token)
        return access_token


def _jwt_exp(token: str) -> float:
    """Best-effort read of a JWT's `exp` claim. Trino enforces the
    signature on its end; we only need the timestamp to decide when to
    refresh. On any decode failure we treat the token as 1-minute-old
    so the next call refreshes immediately."""
    parts = token.split(".")
    if len(parts) < 2:
        return time.time() + 60
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return float(claims.get("exp", time.time() + 60))
    except Exception:
        return time.time() + 60


_TOKENS = _TokenManager()


def query(sql: str) -> pd.DataFrame:
    """Execute a SQL statement against Trino and return a pandas DataFrame.

    Each call fetches a fresh JWT (cached when still valid) and opens
    a new Trino connection — connection overhead is small and this
    keeps the auth flow simple. RBAC (row filters, column masks) is
    applied per query based on the calling user's Keycloak attributes.
    """
    conn = connect(
        host=os.environ["TRINO_HOST"],
        port=int(os.environ["TRINO_PORT"]),
        http_scheme=os.environ["TRINO_SCHEME"],
        user=os.environ["JUPYTERHUB_USER"],
        catalog=os.environ["TRINO_CATALOG"],
        schema=os.environ["TRINO_SCHEMA"],
        auth=JWTAuthentication(_TOKENS.get()),
        verify=os.environ["TRINO_CA_CERT"],
    )
    cur = conn.cursor()
    cur.execute(sql)
    columns = [c[0] for c in cur.description]
    return pd.DataFrame(cur.fetchall(), columns=columns)
