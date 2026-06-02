"""Resolve the identity used by scout.trino against Trino.

Two modes, auto-detected from environment:

  Voila (service-principal impersonation, ADR 0022)
    Set when KEYCLOAK_VOILA_SVC_CLIENT_ID is present. The notebook holds
    no end-user token; the Voila web tier captures the OIDC-authed
    user's preferred_username from each request's
    X-Auth-Request-Access-Token header (via scout.voila) and threads it
    into the kernel env as X_AUTH_REQUEST_ACCESS_TOKEN. We mint a
    voila_svc client_credentials JWT and tell Trino to evaluate AuthZ
    against the impersonated user via X-Trino-User.

  Jupyter (user-token pass-through)
    Set when JUPYTERHUB_API_TOKEN is present. The Hub holds the user's
    Keycloak access token in auth_state; the spawned kernel fetches it
    via the Hub API (GET /users/<user>) using its own JUPYTERHUB_API_TOKEN
    plus the admin:auth_state!user scope granted by the role override.
    No X-Trino-User — Trino reads identity from the JWT's sub claim.

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


def _decode_jwt_claims(token: str) -> dict:
    """Decode JWT claims without signature verification.

    The token was already validated by Keycloak (issuance) and by the
    upstream gatekeeper (oauth2-proxy for Voila, JupyterHub for Jupyter).
    We only need the claims to surface preferred_username + exp.
    """
    try:
        _h, payload_b64, _s = token.split(".")
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return {}


def _is_near_expiry(token: str) -> bool:
    claims = _decode_jwt_claims(token)
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return True
    return time.time() >= (exp - _REFRESH_BEFORE_EXPIRY_SECONDS)


class _VoilaSvcProvider:
    """voila_svc client_credentials token, cached in-process."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._lock = threading.Lock()

    def _mint(self) -> str:
        # No verify= — Keycloak runs behind a publicly-trusted cert via
        # the ingress; the cert-manager CA bundle (TRINO_CA_CERT) is for
        # Trino, not Keycloak.
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
        user = _user_from_forwarded_header() or "anonymous"
        return self._token, user


def _user_from_forwarded_header() -> str:
    token = os.environ.get("X_AUTH_REQUEST_ACCESS_TOKEN", "")
    if not token:
        return ""
    return _decode_jwt_claims(token).get("preferred_username", "")


class _JupyterHubUserProvider:
    """Fetches the user's Keycloak access token via the Hub API.

    Cached in-process; re-fetched when near expiry. The Hub itself
    refreshes the token against Keycloak when refresh_pre_spawn is on
    plus the OAuthenticator's refresh path, so calling GET /users/<user>
    returns a fresh access_token.
    """

    def __init__(self) -> None:
        self._token: str | None = None
        self._lock = threading.Lock()

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
            "scout.trino: no identity source detected. Expected either "
            "KEYCLOAK_VOILA_SVC_CLIENT_ID (Voila) or JUPYTERHUB_API_TOKEN "
            "(Jupyter) in the environment."
        )
    return _singleton


def resolve_identity() -> tuple[str, str | None]:
    """Return (bearer_token, impersonation_user_or_None) for the current env."""
    return _resolve_provider()()


def resolve_audit_user() -> str:
    """Best-effort username for trino-rw audit logging. Never raises;
    falls back to 'anonymous' (trino-rw has no auth — see
    feedback_trino_rw_failopen)."""
    forwarded = _user_from_forwarded_header()
    if forwarded:
        return forwarded
    hub_user = os.environ.get("JUPYTERHUB_USER")
    if hub_user:
        return hub_user
    return "anonymous"
