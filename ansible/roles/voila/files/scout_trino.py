"""Trino connection helper for Voila playbooks.

Mints a `voila_svc` JWT via Keycloak client_credentials and connects to
Trino as that service principal with X-Trino-User set to the OIDC-authed
end-user's preferred_username. Trino's OPA policy permits voila_svc to
impersonate any user; row filters and column masks evaluate against the
impersonated user's Keycloak attributes (ADR 0022).

Mirrors the pattern in ansible/roles/superset/files/superset_trino_auth.py
— same JWTAuthentication + X-Trino-User shape, different sources for
the username (here: HTTP header forwarded by oauth2-proxy into the
kernel env, populated by scout_voila.ScoutMappingKernelManager).

Token caching: a single voila_svc token is reused across all
connections until ~80% of its lifetime has elapsed, then refreshed.
Process-local cache; each Voila worker mints its own.
"""

import base64
import json
import os
import threading
import time

import requests
import trino.dbapi
from trino.auth import JWTAuthentication

_TOKEN_CACHE: dict = {"access_token": None, "expires_at": 0.0}
_TOKEN_LOCK = threading.Lock()
_REFRESH_BEFORE_EXPIRY_SECONDS = 60


def _mint_svc_token() -> str:
    # No explicit verify= — Keycloak runs behind the cluster ingress with
    # a publicly-trusted TLS cert, so requests' default certifi bundle
    # validates it. Passing verify=TRINO_CA_CERT here would force trust
    # against only the Trino cert-manager CA and 401 every token mint.
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
    payload = response.json()
    _TOKEN_CACHE["access_token"] = payload["access_token"]
    _TOKEN_CACHE["expires_at"] = (
        time.time() + payload["expires_in"] - _REFRESH_BEFORE_EXPIRY_SECONDS
    )
    return _TOKEN_CACHE["access_token"]


def _get_svc_token() -> str:
    with _TOKEN_LOCK:
        if _TOKEN_CACHE["access_token"] and time.time() < _TOKEN_CACHE["expires_at"]:
            return _TOKEN_CACHE["access_token"]
        return _mint_svc_token()


def _user_from_access_token() -> str:
    # Decode preferred_username from the per-kernel access token env var.
    # No signature verification — the token was validated by oauth2-proxy's
    # Keycloak provider before the request reached Voila. The decoded value
    # is used only for X-Trino-User; Trino + OPA enforce the actual AuthZ.
    access_token = os.environ.get("X_AUTH_REQUEST_ACCESS_TOKEN", "")
    if not access_token:
        return ""
    try:
        _header, payload_b64, _sig = access_token.split(".")
        payload_b64 += "=" * (-len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        return claims.get("preferred_username", "")
    except (ValueError, KeyError, json.JSONDecodeError):
        return ""


def connect():
    """Return a Trino DB-API connection scoped to the current Voila user.

    Drop-in replacement for `trino.dbapi.connect(...)` calls in playbooks.
    Pulls connection params from env (TRINO_HOST, TRINO_PORT, etc.) and
    sets the auth + impersonation header automatically. Falls back to
    user='anonymous' if no access token is present — OPA's row filters
    then clamp to zero rows (fail-safe).
    """
    user = _user_from_access_token() or "anonymous"
    return trino.dbapi.connect(
        host=os.environ.get("TRINO_HOST", "trino.scout-analytics"),
        port=int(os.environ.get("TRINO_PORT", "8443")),
        http_scheme=os.environ.get("TRINO_SCHEME", "https"),
        catalog=os.environ.get("TRINO_CATALOG", "delta"),
        schema=os.environ.get("TRINO_SCHEMA", "default"),
        auth=JWTAuthentication(_get_svc_token()),
        verify=os.environ.get("TRINO_CA_CERT", True),
        user=user,
    )


def connect_rw():
    """Return a Trino DB-API connection to the write-enabled `trino-rw`
    instance for the specific writes that Voila playbooks need (reviewer
    annotations on default.reports_followup).

    trino-rw is unauthenticated inside its NetworkPolicy boundary — the
    Voila pod must be on the allowlist (see roles/trino/templates/
    values-rw.yaml.j2). The `user` is set to the OIDC-authed user's
    preferred_username so trino-rw's query log records *who* wrote.
    There's no OPA on trino-rw, so this connection bypasses per-user
    AuthZ; the surface is bounded by the playbook code, which only
    issues specific UPDATE/INSERT statements against the working table.

    Falls back to user='anonymous' when no identity can be resolved.
    trino-rw has no auth, so `user` is only an audit label, not a
    credential — failing closed here would block a reviewer's annotation
    save on a transient header-propagation hiccup, which is a worse
    outcome than a less-precise audit entry. A missing forwarded token is
    already logged at request time by scout_voila._scout_voila_get.
    """
    user = _user_from_access_token() or "anonymous"
    return trino.dbapi.connect(
        host=os.environ.get("TRINO_RW_HOST", "trino-rw.scout-extractor"),
        port=int(os.environ.get("TRINO_RW_PORT", "8080")),
        http_scheme="http",
        catalog=os.environ.get("TRINO_CATALOG", "delta"),
        schema=os.environ.get("TRINO_SCHEMA", "default"),
        user=user,
    )
