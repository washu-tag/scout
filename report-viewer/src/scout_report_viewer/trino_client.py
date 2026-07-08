"""Trino client. Service-principal auth + X-Trino-User impersonation (ADR 0022).

The service authenticates to Trino as `report_viewer_svc` and passes
`X-Trino-User: <preferred_username>` on every query so OPA evaluates
filters/masks against the real end user. Same pattern as Superset
and Voila.

The svc access token is cached process-wide and refreshed proactively
when ⅕ of the lifetime remains (ADR 0024 ratio). Refresh is
single-flight under a `threading.Lock`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, time as _time
from decimal import Decimal
from typing import Any, Iterator

import httpx
from trino.auth import JWTAuthentication
from trino.dbapi import connect as trino_connect

from .config import settings

log = logging.getLogger(__name__)


_REFRESH_BEFORE_EXPIRY_FRACTION = 5
_FALLBACK_LIFETIME_SECONDS = 300


class _SvcTokenCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str | None = None
        self._refresh_at: float = 0.0

    def get(self) -> str:
        now = time.time()
        if self._token is not None and now < self._refresh_at:
            return self._token
        with self._lock:
            now = time.time()
            if self._token is not None and now < self._refresh_at:
                return self._token
            self._fetch_locked()
            assert self._token is not None
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._refresh_at = 0.0

    def _fetch_locked(self) -> None:
        if not (
            settings.trino_auth_token_url
            and settings.trino_auth_client_id
            and settings.trino_auth_client_secret
        ):
            raise RuntimeError(
                "report_viewer_svc credentials not configured "
                "(REPORT_VIEWER_TRINO_AUTH_*); cannot mint Trino token"
            )
        resp = httpx.post(
            settings.trino_auth_token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": settings.trino_auth_client_id,
                "client_secret": settings.trino_auth_client_secret,
            },
            timeout=15.0,
            verify=settings.trino_ca_cert or True,
        )
        resp.raise_for_status()
        payload = resp.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("keycloak token response had no access_token")
        lifetime = _token_lifetime(token, payload.get("expires_in"))
        self._token = token
        self._refresh_at = (
            time.time() + lifetime - (lifetime / _REFRESH_BEFORE_EXPIRY_FRACTION)
        )
        log.info(
            "minted report_viewer_svc Trino token",
            extra={"lifetime_s": lifetime, "client_id": settings.trino_auth_client_id},
        )


def _token_lifetime(jwt_token: str, expires_in: int | None) -> int:
    """exp − iat from the JWT, falling back to `expires_in` then a floor."""
    try:
        payload_b64 = jwt_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        iat = payload.get("iat")
        exp = payload.get("exp")
        if isinstance(iat, int) and isinstance(exp, int) and exp > iat:
            return exp - iat
    except Exception:
        log.debug("could not decode JWT for lifetime calc", exc_info=True)
    if isinstance(expires_in, int) and expires_in > 0:
        return expires_in
    return _FALLBACK_LIFETIME_SECONDS


_default_cache = _SvcTokenCache()


@contextmanager
def _connect(user: str | None) -> Iterator[Any]:
    token = _default_cache.get()
    conn = trino_connect(
        host=settings.trino_host,
        port=settings.trino_port,
        http_scheme=settings.trino_scheme,
        # `user=` becomes X-Trino-User → OPA evaluates against this identity.
        user=user or settings.trino_auth_client_id,
        catalog=settings.trino_catalog,
        schema=settings.trino_schema,
        verify=settings.trino_ca_cert or True,
        auth=JWTAuthentication(token),
    )
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            log.debug("trino connection close failed (ignored)", exc_info=True)


def _execute_sync(
    sql: str, user: str | None, params: list | tuple | None
) -> tuple[list[str], list[list[Any]]]:
    with _connect(user) as conn:
        cur = conn.cursor()
        # Driver requires None or non-empty sequence for params (asserts on type).
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description] if cur.description else []
        return columns, rows


def _normalize(value: Any) -> Any:
    """Convert Trino-native types to JSON-friendly Python equivalents.

    trino-python-client returns ROW values as a `NamedRowTuple`
    (subclass of tuple) where the column names are stored in `_names`
    (not `_fields` like collections.namedtuple) and the values are
    stored positionally as the tuple itself. Walk by zip(names,
    self) to build a {name: value} dict.

    Walk arrays / nested rows and turn them into plain dicts so the
    eventual JSON has the field names the SPA expects.
    """
    names = getattr(value, "_names", None)
    if (
        names is not None
        and isinstance(value, tuple)
        and len(names) == len(value)
        and not isinstance(value, (str, bytes))
    ):
        return {
            (n if n is not None else f"_{i}"): _normalize(v)
            for i, (n, v) in enumerate(zip(names, value))
        }
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, tuple):
        return [_normalize(v) for v in value]
    if isinstance(value, (datetime, date, _time, Decimal)):
        return str(value)
    return value


async def execute(
    sql: str,
    user: str | None = None,
    params: list | tuple | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Run `sql` against Trino. `params` is bound positionally (`?`).

    Lists/tuples bind as Trino ARRAY values, so prefer
    `WHERE contains(?, col)` over `WHERE col IN (...)` for matching a
    column against a caller-supplied list (the driver doesn't expand
    list params into IN-clauses).
    """
    columns, raw_rows = await asyncio.to_thread(_execute_sync, sql, user, params)
    dict_rows = [
        {col: _normalize(raw_rows[i][j]) for j, col in enumerate(columns)}
        for i in range(len(raw_rows))
    ]
    return columns, dict_rows
