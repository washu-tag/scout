"""Query-result cache in Valkey (#806).

A key covers the user and query plus the user's permissions fingerprint from OPA
(their bundle record and the policy revision) and the Delta version of every
table the query reads, so a permission, policy or data change makes older
entries unreachable and the TTL removes them. Trino's planner names the input
tables (`EXPLAIN (TYPE IO)`, which sees through views); a query reading anything
outside the Delta catalog has no version to key on and runs uncached.

OPA's replicas pull the user bundle independently, so just after a permission
change the fingerprint read here and the policy Trino applies can disagree. A
result is therefore stored only once a second fingerprint read, at least
`cache_settle_seconds` after the query started, matches the first.

Off unless `valkey_url` and `opa_url` are set and `cache_ttl_seconds` is
positive. Any Valkey, OPA or Trino failure here means running uncached, never a
request error.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import httpx
import redis.asyncio as redis
import sqlglot
from sqlglot.tokens import TokenType

from . import metrics, trino_client
from .config import settings
from .evidence import DIALECT

log = logging.getLogger(__name__)

# Bump the version when the stored encoding changes; old entries expire unread.
_PREFIX = "rv:v1:"

_valkey: redis.Redis | None = None
_opa: httpx.AsyncClient | None = None
# Detached so neither a write's settle wait nor a table or version lookup holds
# up a response or its connection.
_pending: set[asyncio.Task] = set()

# SQL -> (its input tables, when looked up). Tables are None when the lookup
# failed or one can't be versioned; either is retried once the refresh interval
# passes. A query's tables change only if a view over them is redefined.
# Bounded, least recently used out.
_TABLES_MAX = 1024
_Table = tuple[str, str, str]  # catalog, schema, table
_tables: OrderedDict[str, tuple[tuple[_Table, ...] | None, float]] = OrderedDict()
_resolving: set[str] = set()
# Table -> its version, shared by every query reading the table, and when each
# table was last looked up, successfully or not.
_versions: dict[_Table, str] = {}
_checked: dict[_Table, float] = {}
_refreshing: set[_Table] = set()


@dataclass
class Lookup:
    op: str
    user: str
    # None when this request runs uncached (cache off or a dependency unavailable).
    key: str | None = None
    fingerprint: str | None = None
    started: float = 0.0
    value: Any = None


def enabled() -> bool:
    return bool(settings.valkey_url and settings.opa_url) and (
        settings.cache_ttl_seconds > 0
    )


def _valkey_client() -> redis.Redis:
    global _valkey
    if _valkey is None:
        _valkey = redis.from_url(
            settings.valkey_url,
            username=settings.valkey_username or None,
            password=settings.valkey_password or None,
            socket_timeout=settings.valkey_timeout_seconds,
            socket_connect_timeout=settings.valkey_timeout_seconds,
        )
    return _valkey


def _opa_client() -> httpx.AsyncClient:
    global _opa
    if _opa is None:
        _opa = httpx.AsyncClient(
            base_url=settings.opa_url, timeout=settings.opa_timeout_seconds
        )
    return _opa


async def aclose() -> None:
    """Drop pending work (a lost write only costs a later miss) and close."""
    global _valkey, _opa
    for task in list(_pending):
        task.cancel()
    await wait_pending()
    if _valkey is not None:
        await _valkey.aclose()
        _valkey = None
    if _opa is not None:
        await _opa.aclose()
        _opa = None


async def _fingerprint(user: str) -> str | None:
    try:
        resp = await _opa_client().post(
            "/v1/data/scout/cache_fingerprint", json={"input": {"user": user}}
        )
        resp.raise_for_status()
        return resp.json().get("result")
    except Exception:
        log.warning("cache: OPA fingerprint unavailable", exc_info=True)
        return None


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)


def _data_version(user: str, sql: str) -> str | None:
    """The versions of `sql`'s input tables, from what's already known; anything
    missing or stale is looked up in the background, so no request waits on it.
    None while a lookup is outstanding or when the query can't be versioned."""
    now = time.monotonic()
    tables, resolved_at = _tables.get(sql, (None, -math.inf))
    if sql in _tables:
        _tables.move_to_end(sql)
    if tables is None:
        retry = now - resolved_at > settings.cache_version_refresh_seconds
        if retry and sql not in _resolving:
            _resolving.add(sql)
            _spawn(_resolve_tables(user, sql))
        return None
    stale = [
        t
        for t in tables
        if t not in _refreshing
        and now - _checked.get(t, -math.inf) > settings.cache_version_refresh_seconds
    ]
    if stale:
        _refreshing.update(stale)
        _spawn(_refresh_versions(user, stale))
    if any(t not in _versions for t in tables):
        return None
    return json.dumps([[*t, _versions[t]] for t in tables])


def _explainable(sql: str) -> str:
    """`sql` with each bound parameter replaced by an empty ID list. The only
    parameter Report Viewer binds is an uploaded ID array (`contains(?, col)`),
    and its value can't change which tables a query reads."""
    tokens = sqlglot.tokenize(sql, dialect=DIALECT)
    for t in reversed([t for t in tokens if t.token_type == TokenType.PLACEHOLDER]):
        sql = f"{sql[:t.start]}CAST(ARRAY[] AS array(varchar)){sql[t.end + 1:]}"
    return sql


async def _resolve_tables(user: str, sql: str) -> None:
    tables = None
    try:
        _cols, rows = await trino_client.execute(
            f"EXPLAIN (TYPE IO, FORMAT JSON) {_explainable(sql)}", user=user
        )
        plan = json.loads(next(iter(rows[0].values())))
        names: set[_Table] = set()
        for info in plan.get("inputTableColumnInfos", []):
            table = info["table"]
            names.add(
                (
                    table["catalog"],
                    table["schemaTable"]["schema"],
                    table["schemaTable"]["table"],
                )
            )
        if all(catalog == settings.trino_catalog for catalog, _, _ in names):
            tables = tuple(sorted(names))
    except Exception:
        log.warning("cache: input tables unavailable", exc_info=True)
    finally:
        _tables[sql] = (tables, time.monotonic())
        _tables.move_to_end(sql)
        if len(_tables) > _TABLES_MAX:
            _tables.popitem(last=False)
        _resolving.discard(sql)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def _refresh_versions(user: str, tables: list[_Table]) -> None:
    def history(i: int, table: _Table) -> str:
        catalog, schema, name = table
        return (
            f"SELECT {i} AS i, max(version) AS version, max(timestamp) AS ts "
            f"FROM {_quote(catalog)}.{_quote(schema)}.{_quote(name + '$history')}"
        )

    try:
        _cols, rows = await trino_client.execute(
            " UNION ALL ".join(history(i, t) for i, t in enumerate(tables)),
            user=user,
        )
        for row in rows:
            _versions[tables[row["i"]]] = f"{row['version']}:{row['ts']}"
    except Exception:
        log.warning("cache: input-table version refresh failed", exc_info=True)
    finally:
        now = time.monotonic()
        for t in tables:
            _checked[t] = now
        _refreshing.difference_update(tables)


def _key(op: str, *parts: Any) -> str:
    raw = json.dumps(parts, separators=(",", ":"), default=str)
    return f"{_PREFIX}{op}:{hashlib.sha256(raw.encode()).hexdigest()}"


def pack_rows(columns: list[str], rows: list[dict[str, Any]]) -> list[list[Any]]:
    return [[r.get(c) for c in columns] for r in rows]


def unpack_rows(columns: list[str], rows: list[list[Any]]) -> list[dict[str, Any]]:
    return [dict(zip(columns, r)) for r in rows]


def _encode(value: Any) -> bytes:
    return zlib.compress(json.dumps(value, separators=(",", ":")).encode(), 1)


def _decode(blob: bytes) -> Any:
    return json.loads(zlib.decompress(blob))


async def lookup(op: str, user: str, sql: str, *parts: Any) -> Lookup:
    """Find a cached result for this user's `sql`; `parts` covers anything else
    the result depends on. Call just before running the query: `started` is the
    reference point for the settle wait."""
    found = Lookup(op=op, user=user)
    if enabled():
        fingerprint = await _fingerprint(user)
        version = _data_version(user, sql)
        if fingerprint is None or version is None:
            metrics.CACHE_LOOKUPS.labels(op=op, result="unavailable").inc()
        else:
            found.fingerprint = fingerprint
            found.key = _key(op, user, fingerprint, version, sql, *parts)
            found.value = await _get(op, found.key)
    found.started = time.monotonic()
    return found


async def _get(op: str, key: str) -> Any | None:
    try:
        blob = await _valkey_client().get(key)
        value = None if blob is None else await asyncio.to_thread(_decode, blob)
    except Exception:
        log.warning("cache: read failed; running uncached", exc_info=True)
        metrics.CACHE_LOOKUPS.labels(op=op, result="error").inc()
        return None
    result = "miss" if value is None else "hit"
    metrics.CACHE_LOOKUPS.labels(op=op, result=result).inc()
    return value


def remember(found: Lookup, value: Any) -> None:
    """Store `value` under the lookup's key once the settle check passes."""
    if found.key is not None:
        _spawn(_confirm_and_put(found, value))


async def _confirm_and_put(found: Lookup, value: Any) -> None:
    wait = found.started + settings.cache_settle_seconds - time.monotonic()
    if wait > 0:
        await asyncio.sleep(wait)
    if await _fingerprint(found.user) != found.fingerprint:
        metrics.CACHE_WRITES.labels(op=found.op, result="unsettled").inc()
        return
    try:
        blob = await asyncio.to_thread(_encode, value)
        await _valkey_client().set(found.key, blob, ex=settings.cache_ttl_seconds)
    except Exception:
        log.warning("cache: write failed", exc_info=True)
        metrics.CACHE_WRITES.labels(op=found.op, result="error").inc()
        return
    metrics.CACHE_WRITES.labels(op=found.op, result="stored").inc()


async def wait_pending() -> None:
    """Let in-flight writes and lookups finish."""
    if _pending:
        await asyncio.gather(*_pending, return_exceptions=True)
