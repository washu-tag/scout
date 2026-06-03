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


def query(sql_str: str, params: dict | None = None):
    """Run SQL against Scout's Trino. Returns a pandas DataFrame.

    Use `:name` placeholders in the SQL with values in `params`.

    Example:
        query("SELECT * FROM reports WHERE sending_facility = :f",
              params={"f": "BJH"})
    """
    import pandas as pd

    return pd.read_sql(text(sql_str), _get_engine(), params=params or {})
