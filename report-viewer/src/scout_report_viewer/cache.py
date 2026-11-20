"""Query-result cache in Valkey (#806).

A key covers the user and query plus the user's permissions fingerprint from OPA
(their bundle record and the policy revision) and the Delta version of every
table the query reads, so a permission, policy or data change makes older
entries unreachable and the TTL removes them. Trino's planner names the input
tables (`EXPLAIN (TYPE IO)`, which sees through views); a query reading anything
outside the Delta catalog has no version to key on and runs uncached. Versions
are read as Report Viewer's own identity, which OPA exempts from row filters on
`$history` tables only, so every user's searches key on the same real versions.

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
from sqlglot import exp
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
_resolving: dict[str, asyncio.Task] = {}
# Table -> its version, shared by every query reading the table, and when each
# table was last looked up, successfully or not.
_versions: dict[_Table, str] = {}
_checked: dict[_Table, float] = {}
_refreshing: set[_Table] = set()

# Startup read of every table's version, so a search is cacheable from its first
# run. Readiness waits for it, but only up to cache_warm_up_seconds.
_warm_up: asyncio.Task | None = None
_warm_up_started = 0.0


@dataclass
class Lookup:
    op: str
    user: str
    sql: str = ""
    parts: tuple[Any, ...] = ()
    # None when the key isn't known yet, or this request runs uncached (cache off
    # or a dependency unavailable).
    digest: str | None = None
    fingerprint: str | None = None
    # The table versions known at lookup when the key couldn't be built yet: read
    # before the query started, so safe to key a write on once its tables are known.
    known: dict[_Table, str] | None = None
    started: float = 0.0
    value: Any = None

    @property
    def key(self) -> str | None:
        return self.key_for(self.op)

    def key_for(self, op: str) -> str | None:
        """The key another `op` stores for this same query and user."""
        return None if self.digest is None else _key(op, self.digest)

    @property
    def writable(self) -> bool:
        return self.digest is not None or self.known is not None


def _key(op: str, digest: str) -> str:
    return f"{_PREFIX}{op}:{digest}"


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


def _spawn(coro: Any) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    return task


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
            _resolving[sql] = _spawn(_resolve_tables(user, sql))
        return None
    stale = [
        t
        for t in tables
        if t not in _refreshing
        and now - _checked.get(t, -math.inf) > settings.cache_version_refresh_seconds
    ]
    if stale:
        _refreshing.update(stale)
        _spawn(_refresh_versions(stale))
    return _version_of(tables, _versions)


def _version_of(tables: tuple[_Table, ...], versions: dict[_Table, str]) -> str | None:
    if any(t not in versions for t in tables):
        return None
    return json.dumps([[*t, versions[t]] for t in tables])


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
        _resolving.pop(sql, None)
    new = [t for t in tables or () if t not in _versions and t not in _refreshing]
    if new:
        _refreshing.update(new)
        await _refresh_versions(new)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def _refresh_versions(tables: list[_Table], quiet: bool = False) -> None:
    def history(i: int, table: _Table) -> str:
        catalog, schema, name = table
        return (
            f"SELECT {i} AS i, max(version) AS version, max(timestamp) AS ts "
            f"FROM {_quote(catalog)}.{_quote(schema)}.{_quote(name + '$history')}"
        )

    try:
        _cols, rows = await trino_client.execute(
            " UNION ALL ".join(history(i, t) for i, t in enumerate(tables))
        )
        # Every Delta table has a commit, so no history means the read was
        # filtered, and a version recorded from it would never change.
        for row in rows:
            if row["version"] is not None:
                _versions[tables[row["i"]]] = f"{row['version']}:{row['ts']}"
    except Exception:
        if not quiet:
            log.warning("cache: input-table version refresh failed", exc_info=True)
    finally:
        now = time.monotonic()
        for t in tables:
            _checked[t] = now
        _refreshing.difference_update(tables)


def start_warm_up() -> None:
    global _warm_up, _warm_up_started
    if enabled():
        _warm_up_started = time.monotonic()
        _warm_up = _spawn(_read_all_versions())


def warmed_up() -> bool:
    """Whether the startup read has finished, failed, or run out of time."""
    return (
        _warm_up is None
        or _warm_up.done()
        or time.monotonic() - _warm_up_started > settings.cache_warm_up_seconds
    )


async def _read_all_versions() -> None:
    """The version of every table in the configured schema, plus the tables its
    views read, which covers tables hidden from listing (the patient mapping).
    One query per table, since the schema also lists Spark views that have no
    Delta history and would fail a combined query."""
    catalog, schema = settings.trino_catalog, settings.trino_schema
    try:
        _cols, rows = await trino_client.execute(
            f"SELECT table_name FROM {_quote(catalog)}.information_schema.tables "
            "WHERE table_schema = ? AND table_type = 'BASE TABLE'",
            params=[schema],
        )
        _cols, views = await trino_client.execute(
            f"SELECT view_definition FROM {_quote(catalog)}.information_schema.views "
            "WHERE table_schema = ?",
            params=[schema],
        )
    except Exception:
        log.warning("cache: table list unavailable; starting cold", exc_info=True)
        return
    tables = {(catalog, schema, r["table_name"]) for r in rows}
    for v in views:
        tables |= _view_tables(v["view_definition"] or "", catalog, schema)
    limit = asyncio.Semaphore(8)

    async def one(table: _Table) -> None:
        async with limit:
            await _refresh_versions([table], quiet=True)

    await asyncio.gather(*(one(t) for t in sorted(tables)))
    log.info("cache: read %d of %d table versions", len(_versions), len(tables))


def _view_tables(definition: str, catalog: str, schema: str) -> set[_Table]:
    """The tables a view's SQL names, in this catalog, less its CTEs."""
    try:
        tree = sqlglot.parse_one(definition, dialect=DIALECT)
    except Exception:
        return set()
    ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    found = set()
    for t in tree.find_all(exp.Table):
        name = (t.catalog or catalog, t.db or schema, t.name)
        if name[0] == catalog and not (not t.db and t.name in ctes):
            found.add(name)
    return found


def _digest(*parts: Any) -> str:
    raw = json.dumps(parts, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


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
    found = Lookup(op=op, user=user, sql=sql, parts=parts)
    if enabled():
        found.fingerprint = await _fingerprint(user)
        version = _data_version(user, sql)
        if found.fingerprint is None:
            metrics.CACHE_LOOKUPS.labels(op=op, result="unavailable").inc()
        elif version is None:
            found.known = dict(_versions)
            metrics.CACHE_LOOKUPS.labels(op=op, result="cold").inc()
        else:
            found.digest = _digest(user, found.fingerprint, version, sql, *parts)
            found.value = await _get(op, found.key)
    found.started = time.monotonic()
    return found


async def _digest_late(found: Lookup) -> str | None:
    """The key's digest for a lookup that couldn't build one: once the query's
    tables are known, from the versions known at lookup. None if any of them
    wasn't known then, since a version read after the query started could be
    newer than the data it read."""
    if found.digest is not None:
        return found.digest
    if found.known is None:
        return None
    task = _resolving.get(found.sql)
    if task is not None:
        await asyncio.wait([task])
    tables, _ = _tables.get(found.sql, (None, 0.0))
    version = None if tables is None else _version_of(tables, found.known)
    if version is None:
        return None
    return _digest(found.user, found.fingerprint, version, found.sql, *found.parts)


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
    if found.writable:
        _spawn(_confirm_and_put(found, value))


async def _confirm_and_put(found: Lookup, value: Any) -> None:
    wait = found.started + settings.cache_settle_seconds - time.monotonic()
    if wait > 0:
        await asyncio.sleep(wait)
    if await _fingerprint(found.user) != found.fingerprint:
        metrics.CACHE_WRITES.labels(op=found.op, result="unsettled").inc()
        return
    await _put_late(found, found.op, value, settings.cache_ttl_seconds)


async def _put_late(found: Lookup, op: str, value: Any, ttl: int) -> None:
    digest = await _digest_late(found)
    if digest is None:
        metrics.CACHE_WRITES.labels(op=op, result="unversioned").inc()
        return
    await _put(op, _key(op, digest), value, ttl)


async def _put(op: str, key: str, value: Any, ttl: int) -> None:
    try:
        blob = await asyncio.to_thread(_encode, value)
        await _valkey_client().set(key, blob, ex=ttl)
    except Exception:
        log.warning("cache: write failed", exc_info=True)
        metrics.CACHE_WRITES.labels(op=op, result="error").inc()
        return
    metrics.CACHE_WRITES.labels(op=op, result="stored").inc()


def remember_briefly(found: Lookup, op: str, value: Any, ttl: int) -> None:
    """Store `value` under `op`'s key for the same query right away, without the
    settle check. Only for a reader that acts on rows this user was just shown,
    so a result computed under permissions about to change exposes nothing new."""
    if found.writable:
        _spawn(_put_late(found, op, value, ttl))


async def get_related(found: Lookup, op: str) -> Any | None:
    """What `op` stored for the same query and user, if anything."""
    key = found.key_for(op)
    return None if key is None else await _get(op, key)


async def wait_pending() -> None:
    """Let in-flight writes and lookups finish."""
    if _pending:
        await asyncio.gather(*_pending, return_exceptions=True)
