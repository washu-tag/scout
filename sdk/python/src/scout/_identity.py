"""Resolve the identity the scout SDK uses against Trino.

Two modes, auto-detected from environment:

  Voila (service-principal impersonation, ADR 0022)
    Set when KEYCLOAK_VOILA_SVC_CLIENT_ID is present. The kernel never
    sees the end-user's bearer token; the Voila runtime (voila_runtime,
    shipped with the Voila chart) threads only the preferred_username
    into the kernel env as
    X_AUTH_REQUEST_PREFERRED_USERNAME (sourced from oauth2-proxy's
    X-Auth-Request-Preferred-Username response header). We mint a
    voila_svc client_credentials JWT and tell Trino to evaluate AuthZ
    against the impersonated user via X-Trino-User.

  Jupyter (user-token pass-through)
    Set when JUPYTERHUB_API_TOKEN is present. The Hub holds the user's
    Keycloak access token in auth_state; the spawned kernel fetches it
    via the Hub API (GET /users/<user>) using its own JUPYTERHUB_API_TOKEN
    plus the admin:auth_state!user scope granted by the role override.
    No X-Trino-User - Trino reads identity from the JWT's sub claim.

Both providers refresh themselves transparently on near-expiry.
"""

import base64
import json
import logging
import os
import threading
import time
from typing import Callable

import requests

logger = logging.getLogger(__name__)

_REFRESH_BEFORE_EXPIRY_SECONDS = 60

# Optional override; tests set this to inject a fake provider.
_override_provider: Callable[[], tuple[str, str | None]] | None = None


def _is_near_expiry(token: str) -> bool:
    """Decode the JWT's exp claim (unverified) and check if it's near expiry.

    The token was already validated upstream (Keycloak at issuance,
    JupyterHub at fetch); we only need the claim to time cache refresh.
    """
    try:
        _h, payload_b64, _s = token.split(".")
        payload_b64 += "=" * (-len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return True
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return True
    return time.time() >= (exp - _REFRESH_BEFORE_EXPIRY_SECONDS)


class _VoilaSvcProvider:
    """voila_svc client_credentials token, cached in-process. The
    impersonation user comes from X_AUTH_REQUEST_PREFERRED_USERNAME,
    which the Voila runtime (voila_runtime) injected per-request."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._lock = threading.Lock()

    def _mint(self) -> str:
        # No verify=: Scout points KEYCLOAK_TOKEN_URL at the internal
        # in-cluster Keycloak (keycloak_internal_token_url, plain HTTP), so
        # there's no TLS on this hop. If a deployment overrides it with an
        # HTTPS endpoint, requests falls back to the system CA bundle
        # (TRINO_CA_CERT is Trino-specific and deliberately not reused here).
        response = requests.post(
            os.environ["KEYCLOAK_TOKEN_URL"],
            data={
                "grant_type": "client_credentials",
                "client_id": os.environ["KEYCLOAK_VOILA_SVC_CLIENT_ID"],
                "client_secret": os.environ["KEYCLOAK_VOILA_SVC_CLIENT_SECRET"],
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def __call__(self) -> tuple[str, str | None]:
        with self._lock:
            if not self._token or _is_near_expiry(self._token):
                self._token = self._mint()
        user = os.environ.get("X_AUTH_REQUEST_PREFERRED_USERNAME", "") or "anonymous"
        return self._token, user


class _JupyterHubUserProvider:
    """Fetches the user's Keycloak access token via the Hub API.

    Cached in-process; re-fetched when near expiry. The Hub itself
    refreshes the token against Keycloak when refresh_pre_spawn is on
    plus the OAuthenticator's refresh path, so calling GET /users/<user>
    normally returns a fresh access_token.

    Caveat: the Hub controls *when* it refreshes against Keycloak — we
    can't force it from the kernel. If the Hub hands back a token that is
    already at/near expiry (e.g. the refresh token itself expired or the
    Hub's auth-refresh age hasn't elapsed), queries can still fail with
    401 until the user re-authenticates to JupyterHub. We warn (once) when
    we detect that state rather than retrying — the fix is a fresh Hub
    login, not another fetch.
    """

    def __init__(self) -> None:
        self._token: str | None = None
        self._lock = threading.Lock()
        self._warned_stale = False

    def _fetch(self) -> str:
        api_url = os.environ["JUPYTERHUB_API_URL"].rstrip("/")
        api_token = os.environ["JUPYTERHUB_API_TOKEN"]
        user = os.environ["JUPYTERHUB_USER"]
        response = requests.get(
            f"{api_url}/users/{user}",
            headers={"Authorization": f"token {api_token}"},
            timeout=10,
        )
        response.raise_for_status()
        auth_state = response.json().get("auth_state") or {}
        access_token = auth_state.get("access_token")
        if not access_token:
            raise RuntimeError(
                "JupyterHub auth_state has no access_token; check that "
                "enable_auth_state=true is set and the kernel's API token "
                "has admin:auth_state!user."
            )
        if _is_near_expiry(access_token) and not self._warned_stale:
            self._warned_stale = True
            logger.warning(
                "JupyterHub returned an access token that is already at/near "
                "expiry; Trino queries may fail with 401. If they do, log out "
                "and back in to JupyterHub to refresh your session."
            )
        return access_token

    def __call__(self) -> tuple[str, str | None]:
        with self._lock:
            if not self._token or _is_near_expiry(self._token):
                self._token = self._fetch()
        return self._token, None


_singleton: Callable[[], tuple[str, str | None]] | None = None


def _resolve_provider() -> Callable[[], tuple[str, str | None]]:
    global _singleton
    if _override_provider is not None:
        return _override_provider
    if _singleton is not None:
        return _singleton
    if "KEYCLOAK_VOILA_SVC_CLIENT_ID" in os.environ:
        _singleton = _VoilaSvcProvider()
    elif "JUPYTERHUB_API_TOKEN" in os.environ:
        _singleton = _JupyterHubUserProvider()
    else:
        raise RuntimeError(
            "scout: no identity source detected. Expected either "
            "KEYCLOAK_VOILA_SVC_CLIENT_ID (Voila) or JUPYTERHUB_API_TOKEN "
            "(Jupyter) in the environment."
        )
    return _singleton


def resolve_identity() -> tuple[str, str | None]:
    """Return (bearer_token, impersonation_user_or_None) for the current env."""
    return _resolve_provider()()


def resolve_audit_user() -> str:
    """Best-effort username for trino-rw audit logging. Never raises;
    falls back to 'anonymous' (trino-rw has no auth - see
    feedback_trino_rw_failopen)."""
    voila_user = os.environ.get("X_AUTH_REQUEST_PREFERRED_USERNAME", "")
    if voila_user:
        return voila_user
    hub_user = os.environ.get("JUPYTERHUB_USER")
    if hub_user:
        return hub_user
    return "anonymous"
