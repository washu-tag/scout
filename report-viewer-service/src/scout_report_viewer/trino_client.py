"""Thin Trino client with JWT pass-through (ADR 0022).

Each call forwards the end-user's Keycloak access token as Bearer to
Trino, so Trino's principal IS the requester and OPA evaluates row
filters / column masks against that identity. Matches Jupyter's
pattern — no service principal, no impersonation.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from typing import Any, Iterator

from trino.auth import JWTAuthentication
from trino.dbapi import connect as trino_connect

from .config import settings

log = logging.getLogger(__name__)


@contextmanager
def _connect(user: str | None, token: str | None) -> Iterator[Any]:
    auth = JWTAuthentication(token) if token else None
    conn = trino_connect(
        host=settings.trino_host,
        port=settings.trino_port,
        http_scheme=settings.trino_scheme,
        user=user or "searches",
        catalog=settings.trino_catalog,
        schema=settings.trino_schema,
        verify=settings.trino_ca_cert or True,
        auth=auth,
    )
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            log.debug("trino connection close failed (ignored)", exc_info=True)


def _execute_sync(
    sql: str, user: str | None, token: str | None
) -> tuple[list[str], list[list[Any]]]:
    with _connect(user, token) as conn:
        cur = conn.cursor()
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
    # Trino NamedRowTuple — tuple subclass with `_names` attribute
    # carrying the column names in tuple-position order.
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
        # Anonymous ROW or plain tuple — return as list.
        return [_normalize(v) for v in value]
    return value


async def execute(
    sql: str,
    user: str | None = None,
    token: str | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Run `sql`, return (columns, rows-as-dicts). Off the event loop.

    ROW-typed columns are normalized to dicts so JSON serialization
    downstream produces the field-name shape the SPA expects (rather
    than the named-tuple repr string).
    """
    columns, raw_rows = await asyncio.to_thread(_execute_sync, sql, user, token)
    dict_rows = [
        {col: _normalize(raw_rows[i][j]) for j, col in enumerate(columns)}
        for i in range(len(raw_rows))
    ]
    return columns, dict_rows
