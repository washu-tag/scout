"""Persistence layer for searches.

A search row is just metadata + the saved SQL. Nothing about which
rows match is persisted (see ADR 0026); /rows wraps `sql` as
a subquery and Trino evaluates on demand. `row_count` is cached at
create time via a single COUNT(*) wrap so the homepage list + search
summary don't need to re-run the query just to display "1,151 rows".

Every mutation is keyed by (search_id, owner_sub) so an authenticated
user can never reach another user's search.
"""

from __future__ import annotations

import logging
from typing import Any

from . import metrics
from .db import get_conn

log = logging.getLogger(__name__)


async def insert_search(
    *,
    search_id: str,
    id_column: str,
    sql: str,
    owner_sub: str,
    row_count: int | None,
    highlight_terms: list[str] | None = None,
    highlight_diagnosis: list[str] | None = None,
    sql_explanation: str | None = None,
    owui_chat_id: str | None = None,
) -> dict[str, Any]:
    """Persist a new search row. `row_count` is the cached COUNT(*)
    from wrapping `sql`; can be None if the caller didn't
    pre-compute it (in which case the SPA falls back to reading total
    from the /rows response)."""
    with metrics.time_postgres("insert_search"):
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO searches
                      (id, id_column, sql, sql_explanation,
                       highlight_terms, highlight_diagnosis, row_count,
                       owner_sub, owui_chat_id)
                    VALUES
                      (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, id_column, sql, sql_explanation,
                              highlight_terms, highlight_diagnosis, row_count,
                              owner_sub, owui_chat_id, created_at
                    """,
                    (
                        search_id,
                        id_column,
                        sql,
                        sql_explanation or "",
                        highlight_terms or [],
                        highlight_diagnosis or [],
                        row_count,
                        owner_sub,
                        owui_chat_id or "",
                    ),
                )
                row = await cur.fetchone()
            await conn.commit()
    assert row is not None
    return _row_to_dict(row)


async def get_search(search_id: str, owner_sub: str | None) -> dict[str, Any] | None:
    """Fetch a search by id, scoped to `owner_sub` (rows with a different
    owner return None - callers should 404). `owner_sub=None` skips the
    check; reserved for service-internal callers, not request handlers."""
    with metrics.time_postgres("get_search"):
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                if owner_sub is None:
                    await cur.execute(
                        """
                        SELECT id, id_column, sql, sql_explanation,
                               highlight_terms, highlight_diagnosis, row_count,
                               owner_sub, owui_chat_id, created_at
                        FROM searches
                        WHERE id = %s
                        """,
                        (search_id,),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT id, id_column, sql, sql_explanation,
                               highlight_terms, highlight_diagnosis, row_count,
                               owner_sub, owui_chat_id, created_at
                        FROM searches
                        WHERE id = %s
                          AND owner_sub = %s
                        """,
                        (search_id, owner_sub),
                    )
                row = await cur.fetchone()
                if row is None:
                    return None
    return _row_to_dict(row)


async def delete_search(search_id: str, owner_sub: str) -> bool:
    """Delete a search by id, scoped to `owner_sub`. Returns True if a
    row was removed, False if no matching row existed (wrong id, or
    owned by someone else)."""
    with metrics.time_postgres("delete_search"):
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM searches WHERE id = %s AND owner_sub = %s",
                    (search_id, owner_sub),
                )
                deleted = cur.rowcount
            await conn.commit()
    return deleted > 0


async def list_searches(owner_sub: str, *, limit: int = 200) -> list[dict[str, Any]]:
    """Return the caller's searches, newest first. Excludes the sql
    blob to keep the listing payload small - the SPA homepage only needs
    metadata and the row count."""
    with metrics.time_postgres("list_searches"):
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, id_column, sql, sql_explanation,
                           highlight_terms, highlight_diagnosis, row_count,
                           owner_sub, owui_chat_id, created_at
                    FROM searches
                    WHERE owner_sub = %s
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
        id_column,
        sql,
        sql_explanation,
        highlight_terms,
        highlight_diagnosis,
        row_count,
        owner_sub,
        owui_chat_id,
        created_at,
    ) = row
    return {
        "id": id_,
        "id_column": id_column,
        "sql": sql,
        "sql_explanation": sql_explanation or "",
        "highlight_terms": highlight_terms or [],
        "highlight_diagnosis": highlight_diagnosis or [],
        "count": row_count if row_count is not None else 0,
        "owner_sub": owner_sub,
        "owui_chat_id": owui_chat_id or "",
        "created_at": created_at,
    }
