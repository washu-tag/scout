"""Trino connection internals for the scout SDK.

Backs the public surface (scout.query / scout.connect):

    import scout
    df = scout.query("SELECT count(*) FROM reports")

Identity source is detected from the environment by scout._identity:
  * Voila kernels -> voila_svc JWT + X-Trino-User impersonation
  * Jupyter kernels -> user's Keycloak access token (no impersonation)

Use `:name` placeholders for parameters; SQLAlchemy expands them. For
list values, prefer Trino's `contains(array, element)` over IN — the
SQLAlchemy dialect doesn't expand list params into IN clauses.
"""

import os
from typing import Any

import requests.auth
import trino.dbapi
import trino.exceptions
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from trino.auth import Authentication

from . import _identity


class _DynamicBearerAuth(requests.auth.AuthBase):
    """requests Auth that re-fetches the bearer on every request, so we
    pick up rotations the underlying provider performs out-of-band."""

    def __init__(self, provider):
        self._provider = provider

    def __call__(self, request):
        token, _ = self._provider()
        request.headers["Authorization"] = f"Bearer {token}"
        return request


class _DynamicJWTAuthentication(Authentication):
    """Trino Authentication that delegates token refresh to a provider."""

    def __init__(self, provider) -> None:
        self._provider = provider

    def set_http_session(self, http_session):
        # Pin TLS verification to this session's CA (TRINO_CA_CERT). With
        # trust_env on, requests substitutes REQUESTS_CA_BUNDLE/CURL_CA_BUNDLE
        # for the request-level verify, and that substitution OVERRIDES
        # session.verify — on package-proxy deployments (ADR 0017) those vars
        # point at the staging CA bundle, which doesn't carry Trino's
        # cert-manager CA, so every query died with CERTIFICATE_VERIFY_FAILED.
        # Trino is in-cluster; env proxies/netrc never apply on this hop.
        http_session.trust_env = False
        http_session.auth = _DynamicBearerAuth(self._provider)
        return http_session

    def get_exceptions(self):
        return ()


def _trino_host() -> str:
    return os.environ.get("TRINO_HOST", "trino.scout-analytics")


def _trino_port() -> int:
    return int(os.environ.get("TRINO_PORT", "8443"))


def _trino_scheme() -> str:
    return os.environ.get("TRINO_SCHEME", "https")


def _trino_verify() -> Any:
    return os.environ.get("TRINO_CA_CERT", True)


def _catalog() -> str:
    return os.environ.get("TRINO_CATALOG", "delta")


def _schema() -> str:
    return os.environ.get("TRINO_SCHEMA", "default")


def _auth_args(provider) -> dict[str, Any]:
    """Auth + TLS + optional impersonation user, shared by connect() and the
    cached engine.

    Whether `user` is set is the entire identity distinction:
      * Voila    -> provider returns the impersonated username; we set it and
                    the trino client sends it as X-Trino-User.
      * Jupyter  -> provider returns None; we omit `user`. The trino client
                    then emits no X-Trino-User (requests drops the None-valued
                    header on its Session merge), so Trino derives the session
                    user from the JWT principal (jwt.principal-field=
                    preferred_username) instead.

    Do NOT backfill a default user here: a stray X-Trino-User would turn the
    Jupyter pass-through into an impersonation attempt by the JWT principal.
    """
    _, user = provider()
    args: dict[str, Any] = {
        "auth": _DynamicJWTAuthentication(provider),
        "http_scheme": _trino_scheme(),
        "verify": _trino_verify(),
    }
    if user:
        args["user"] = user
    return args


def connect() -> trino.dbapi.Connection:
    """Return a Trino DB-API connection scoped to the current user.

    Drop-in for `trino.dbapi.connect(...)`. Pulls TRINO_* params from
    env and attaches the right auth + impersonation header.

    Auth is still refreshed proactively per request (the connection's
    bearer is re-fetched each call), but the reactive 401 retry that
    `query()` provides can't wrap caller-driven `cursor.execute()`; for the
    rare in-flight-expiry case on this path, re-run the failing statement.
    """
    provider = _identity._resolve_provider()
    return trino.dbapi.connect(
        host=_trino_host(),
        port=_trino_port(),
        catalog=_catalog(),
        schema=_schema(),
        **_auth_args(provider),
    )


_engine: Engine | None = None


def _get_engine() -> Engine:
    """Cached SQLAlchemy engine that re-issues the bearer per request
    via _DynamicJWTAuthentication, so long-lived sessions survive
    token rotation without engine.dispose()."""
    global _engine
    if _engine is not None:
        return _engine
    provider = _identity._resolve_provider()
    _engine = create_engine(
        f"trino://{_trino_host()}:{_trino_port()}/{_catalog()}/{_schema()}",
        connect_args=_auth_args(provider),
    )
    return _engine


# Trino raises HttpError("error <status>: <body>") for non-2xx (see
# trino.client.TrinoRequest.raise_response_error). Only 401 (Unauthorized)
# means the bearer itself was rejected -- expired/invalid JWT -- which a fresh
# token can fix. 403 (Forbidden) is an authorization denial: the identity is
# authenticated but not permitted, so re-minting the same identity's token
# changes nothing; let it surface rather than retrying.
_UNAUTHORIZED_MARKER = "error 401"


def _is_unauthorized(exc: BaseException) -> bool:
    """True if exc, or any error in its cause/context chain, is a Trino
    HTTP 401 -- the bearer was rejected. SQLAlchemy/pandas wrap the original
    trino exception, so we walk the chain rather than matching the top-level
    type. 403 (authorization denial) is deliberately excluded: a fresh token
    for the same identity wouldn't change the decision."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        is_http = isinstance(cur, trino.exceptions.HttpError)
        if is_http and _UNAUTHORIZED_MARKER in str(cur):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _with_auth_retry(run):
    """Run `run()`; if Trino rejects the bearer with 401, drop the cached
    token and retry exactly once.

    The reactive half of the proactive+reactive token-refresh strategy
    (ADR 0024). Proactive refresh (the provider's near-expiry re-fetch plus
    the Hub's eager-rotation hook) prevents almost all expiries; this catches
    the residual -- clock skew against Trino's zero-skew JWT check, or a token
    that expired in-flight. The retry re-fetches a fresh bearer (Voila
    re-mints; Jupyter re-pulls from the Hub, which rotates), so a transient
    401 self-heals instead of surfacing to the notebook. A 403 is an
    authorization denial, not a stale credential, so it is not retried."""
    try:
        return run()
    except Exception as exc:
        if not _is_unauthorized(exc):
            raise
        _identity.invalidate()
        return run()


def query(sql_str: str, params: dict | None = None):
    """Run SQL against Scout's Trino. Returns a pandas DataFrame.

    Use `:name` placeholders in the SQL with values in `params`.

    Example:
        query("SELECT * FROM reports WHERE sending_facility = :f",
              params={"f": "BJH"})
    """
    import pandas as pd

    engine = _get_engine()
    return _with_auth_retry(
        lambda: pd.read_sql(text(sql_str), engine, params=params or {})
    )
