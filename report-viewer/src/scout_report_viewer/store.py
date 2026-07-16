"""Persistence layer for searches."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends
from psycopg_pool import AsyncConnectionPool

from . import metrics
from .db import get_pool

log = logging.getLogger(__name__)


class SearchStore:
    """Owner-scoped CRUD over the `searches` table."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def insert_search(
        self,
        *,
        search_id: str,
        id_column: str,
        sql: str,
        owner_sub: str,
        row_count: int | None,
        match_terms: list[str] | None = None,
        match_diagnoses: list[str] | None = None,
        sql_explanation: str | None = None,
        owui_chat_id: str | None = None,
        uploaded_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        with metrics.time_postgres("insert_search"):
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        INSERT INTO searches
                          (id, id_column, sql, sql_explanation,
                           match_terms, match_diagnoses, row_count,
                           uploaded_ids, owner_sub, owui_chat_id)
                        VALUES
                          (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id, id_column, sql, sql_explanation,
                                  match_terms, match_diagnoses, row_count,
                                  uploaded_ids, owner_sub, owui_chat_id, created_at
                        """,
                        (
                            search_id,
                            id_column,
                            sql,
                            sql_explanation or "",
                            match_terms or [],
                            match_diagnoses or [],
                            row_count,
                            uploaded_ids,
                            owner_sub,
                            owui_chat_id or "",
                        ),
                    )
                    row = await cur.fetchone()
                await conn.commit()
        assert row is not None
        return _row_to_dict(row)

    async def get_search(self, search_id: str, owner_sub: str) -> dict[str, Any] | None:
        """Owner-scoped fetch. Returns None when no row matches (wrong id or
        wrong owner) so callers can 404 without distinguishing the two."""
        with metrics.time_postgres("get_search"):
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT id, id_column, sql, sql_explanation,
                               match_terms, match_diagnoses, row_count,
                               uploaded_ids, owner_sub, owui_chat_id, created_at
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

    async def delete_search(self, search_id: str, owner_sub: str) -> bool:
        """Owner-scoped delete. Returns True if a row was removed."""
        with metrics.time_postgres("delete_search"):
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "DELETE FROM searches WHERE id = %s AND owner_sub = %s",
                        (search_id, owner_sub),
                    )
                    deleted = cur.rowcount
                await conn.commit()
        return deleted > 0

    async def list_searches(
        self, owner_sub: str, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Return the caller's searches, newest first."""
        with metrics.time_postgres("list_searches"):
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT id, id_column, sql, sql_explanation,
                               match_terms, match_diagnoses, row_count,
                               uploaded_ids, owner_sub, owui_chat_id, created_at
                        FROM searches
                        WHERE owner_sub = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (owner_sub, limit),
                    )
                    rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]


def get_store(pool: AsyncConnectionPool = Depends(get_pool)) -> SearchStore:
    return SearchStore(pool)


def _row_to_dict(row: tuple) -> dict[str, Any]:
    (
        id_,
        id_column,
        sql,
        sql_explanation,
        match_terms,
        match_diagnoses,
        row_count,
        uploaded_ids,
        owner_sub,
        owui_chat_id,
        created_at,
    ) = row
    return {
        "id": id_,
        "id_column": id_column,
        "sql": sql,
        "sql_explanation": sql_explanation or "",
        "match_terms": match_terms or [],
        "match_diagnoses": match_diagnoses or [],
        "count": row_count if row_count is not None else 0,
        "uploaded_ids": uploaded_ids,
        "owner_sub": owner_sub,
        "owui_chat_id": owui_chat_id or "",
        "created_at": created_at,
    }
