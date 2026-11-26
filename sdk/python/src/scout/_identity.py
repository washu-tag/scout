"""Resolve the bearer the scout SDK sends to Trino.

The user's own Keycloak access token, passed through from JupyterHub (needs
JUPYTERHUB_API_TOKEN in the environment). The Hub holds the token in
auth_state; the spawned kernel fetches it via the Hub API (GET /users/<user>)
using its own JUPYTERHUB_API_TOKEN plus the admin:auth_state!user scope granted
by the role override. No X-Trino-User: Trino reads identity from the JWT.

The provider refreshes the token transparently on near-expiry.
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

_REFRESH_BEFORE_EXPIRY_FRACTION = 0.2  # refresh once <20% of the token's life remains
_REFRESH_BEFORE_EXPIRY_SECONDS = 60  # fixed fallback when the token carries no iat

# Optional override; tests set this to inject a fake provider.
_override_provider: Callable[[], str] | None = None


def _is_near_expiry(token: str) -> bool:
    """Decode the JWT (unverified) and check whether it's time to refresh.

    Refresh once less than _REFRESH_BEFORE_EXPIRY_FRACTION of the token's
    lifetime (exp - iat) remains, so the buffer scales with the token lifespan
    instead of being a fixed offset (ADR 0024). Tokens with no iat fall back to
    the fixed _REFRESH_BEFORE_EXPIRY_SECONDS.

    The token was already validated upstream (Keycloak at issuance, JupyterHub
    at fetch); we only read the claims to time the cache refresh, and fail
    toward "stale" so an unreadable token is re-fetched rather than pinned.
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
    iat = claims.get("iat")
    if isinstance(iat, (int, float)) and exp > iat:
        buffer = _REFRESH_BEFORE_EXPIRY_FRACTION * (exp - iat)
    else:
        buffer = _REFRESH_BEFORE_EXPIRY_SECONDS
    return time.time() >= exp - buffer


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

    def __call__(self) -> str:
        with self._lock:
            if not self._token or _is_near_expiry(self._token):
                self._token = self._fetch()
        return self._token

    def invalidate(self) -> None:
        """Drop the cached token so the next call re-fetches from the Hub.
        Called by the query layer's reactive retry after Trino rejects the
        bearer; with the Hub's eager-rotation hook the re-fetch yields a
        freshly rotated token."""
        with self._lock:
            self._token = None


_singleton: Callable[[], str] | None = None


def invalidate() -> None:
    """Drop the active provider's cached bearer so the next resolve_identity()
    re-fetches. No-op if no provider has been resolved yet."""
    provider = _override_provider or _singleton
    if provider is not None and hasattr(provider, "invalidate"):
        provider.invalidate()


def _resolve_provider() -> Callable[[], str]:
    global _singleton
    if _override_provider is not None:
        return _override_provider
    if _singleton is not None:
        return _singleton
    if "JUPYTERHUB_API_TOKEN" not in os.environ:
        raise RuntimeError(
            "scout: no identity source detected. Expected JUPYTERHUB_API_TOKEN "
            "(a JupyterHub notebook) in the environment."
        )
    _singleton = _JupyterHubUserProvider()
    return _singleton


def resolve_identity() -> str:
    """Return the bearer token for the current env."""
    return _resolve_provider()()


def resolve_audit_user() -> str:
    """Best-effort username: the JupyterHub user. Never raises; falls back to
    'anonymous'."""
    hub_user = os.environ.get("JUPYTERHUB_USER")
    if hub_user:
        return hub_user
    return "anonymous"
