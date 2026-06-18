"""Persistence layer for searches — pure Postgres reads/writes.

A search row is just metadata + the saved SQL. Nothing about which
rows match is persisted (see ADR 0026); /rows wraps `source_sql` as
a subquery and Trino evaluates on demand. `row_count` is cached at
create time via a single COUNT(*) wrap so the homepage list + search
summary don't need to re-run the query just to display "1,151 rows".

Every mutation is keyed by (search_id, owner_sub) so an authenticated
user can never reach another user's search.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from . import metrics
from .config import settings
from .db import get_conn

log = logging.getLogger(__name__)


def _ttl_expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=settings.search_ttl_days)


async def insert_search(
    *,
    search_id: str,
    kind: str,
    id_column: str,
    source_sql: str,
    owner_sub: str,
    row_count: int | None,
    parent_id: str | None = None,
    highlight_terms: list[str] | None = None,
    sql_explanation: str | None = None,
    owui_chat_id: str | None = None,
    owui_chat_title: str | None = None,
) -> dict[str, Any]:
    """Persist a new search row. `row_count` is the cached COUNT(*)
    from wrapping `source_sql`; can be None if the caller didn't
    pre-compute it (in which case the SPA falls back to reading total
    from the /rows response)."""
    expires_at = _ttl_expires_at()
    with metrics.time_postgres("insert_search"):
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO searches
                      (id, kind, id_column, source_sql, sql_explanation,
                       highlight_terms, row_count, parent_id, owner_sub,
                       owui_chat_id, owui_chat_title, expires_at)
                    VALUES
                      (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, kind, id_column, source_sql, sql_explanation,
                              highlight_terms, row_count, parent_id, owner_sub,
                              owui_chat_id, owui_chat_title, created_at,
                              expires_at, last_read_at
                    """,
                    (
                        search_id,
                        kind,
                        id_column,
                        source_sql,
                        sql_explanation or "",
                        highlight_terms or [],
                        row_count,
                        parent_id,
                        owner_sub,
                        owui_chat_id or "",
                        owui_chat_title or "",
                        expires_at,
                    ),
                )
                row = await cur.fetchone()
            await conn.commit()
    assert row is not None
    return _row_to_dict(row)


async def get_search(
    search_id: str, owner_sub: str | None, *, touch: bool = True
) -> dict[str, Any] | None:
    """Fetch a search by id. `owner_sub=None` skips the ownership check,
    treating the URL as a capability — used by the read-only viewer
    endpoints (iframe context, no cookie auth available). The search_id
    is 56 bits of entropy, so direct enumeration is impractical within
    the TTL window. `touch=True` slides the expiry via last_read_at."""
    with metrics.time_postgres("get_search"):
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                if owner_sub is None:
                    await cur.execute(
                        """
                        SELECT id, kind, id_column, source_sql, sql_explanation,
                               highlight_terms, row_count, parent_id, owner_sub,
                               owui_chat_id, owui_chat_title, created_at,
                               expires_at, last_read_at
                        FROM searches
                        WHERE id = %s
                          AND expires_at > now()
                        """,
                        (search_id,),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT id, kind, id_column, source_sql, sql_explanation,
                               highlight_terms, row_count, parent_id, owner_sub,
                               owui_chat_id, owui_chat_title, created_at,
                               expires_at, last_read_at
                        FROM searches
                        WHERE id = %s
                          AND owner_sub = %s
                          AND expires_at > now()
                        """,
                        (search_id, owner_sub),
                    )
                row = await cur.fetchone()
                if row is None:
                    return None
                if touch:
                    new_expiry = _ttl_expires_at()
                    await cur.execute(
                        """
                        UPDATE searches
                           SET last_read_at = now(),
                               expires_at = greatest(expires_at, %s)
                         WHERE id = %s
                        """,
                        (new_expiry, search_id),
                    )
            await conn.commit()
    return _row_to_dict(row)


async def list_searches(owner_sub: str, *, limit: int = 200) -> list[dict[str, Any]]:
    """Return the caller's non-expired searches, newest first. Excludes
    the source_sql blob to keep the listing payload small — the SPA
    homepage only needs metadata and the row count."""
    with metrics.time_postgres("list_searches"):
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, kind, id_column, source_sql, sql_explanation,
                           highlight_terms, row_count, parent_id, owner_sub,
                           owui_chat_id, owui_chat_title, created_at,
                           expires_at, last_read_at
                    FROM searches
                    WHERE owner_sub = %s
                      AND expires_at > now()
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (owner_sub, limit),
                )
                rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: tuple) -> dict[str, Any]:
    (
        id_,
        kind,
        id_column,
        source_sql,
        sql_explanation,
        highlight_terms,
        row_count,
        parent_id,
        owner_sub,
        owui_chat_id,
        owui_chat_title,
        created_at,
        expires_at,
        last_read_at,
    ) = row
    return {
        "id": id_,
        "kind": kind,
        "id_column": id_column,
        "source_sql": source_sql,
        "sql_explanation": sql_explanation or "",
        "highlight_terms": highlight_terms or [],
        "count": row_count if row_count is not None else 0,
        "parent_id": parent_id,
        "owner_sub": owner_sub,
        "owui_chat_id": owui_chat_id or "",
        "owui_chat_title": owui_chat_title or "",
        "created_at": created_at,
        "expires_at": expires_at,
        "last_read_at": last_read_at,
    }
