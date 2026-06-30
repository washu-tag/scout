"""Postgres access - psycopg3 connection pool + the searches schema.

We use raw SQL via psycopg rather than SQLAlchemy + Alembic for Phase 1.
The schema is small enough that an idempotent `ensure_schema()` at startup
is simpler than carrying migration tooling. If/when columns start changing,
swap to Alembic.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import psycopg
from psycopg_pool import AsyncConnectionPool

from .config import settings

log = logging.getLogger(__name__)

SCHEMA_SQL = """
-- Searches are saved SQL only. /rows wraps sql as a subquery
-- and applies pagination/sort/filter at the Trino layer on every read.
-- See ADR 0026.
CREATE TABLE IF NOT EXISTS searches (
  id                  TEXT PRIMARY KEY,
  id_column           TEXT NOT NULL,
  sql                 TEXT NOT NULL,
  sql_explanation     TEXT NOT NULL DEFAULT '',
  highlight_terms     TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  highlight_diagnosis TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  row_count           INTEGER,
  owner_sub           TEXT NOT NULL,
  owui_chat_id        TEXT NOT NULL DEFAULT '',
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS searches_owner_created_idx
  ON searches (owner_sub, created_at DESC);
"""


_pool: AsyncConnectionPool | None = None


async def open_pool() -> AsyncConnectionPool:
    """Lazy-init a process-wide AsyncConnectionPool."""
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=10,
            open=False,
        )
        # Wait at most a few seconds for the first connection at startup
        # so an unreachable DB fails fast (test environment, cold cluster
        # boot) instead of hanging behind psycopg's default 30s/3-attempt
        # retry. /healthz keeps answering; per-request handlers re-open.
        await _pool.open(wait=True, timeout=5.0)
        log.info("opened postgres pool")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("closed postgres pool")


@asynccontextmanager
async def get_conn() -> AsyncIterator[psycopg.AsyncConnection]:
    pool = await open_pool()
    async with pool.connection() as conn:
        yield conn


async def ensure_schema() -> None:
    """Run idempotent CREATE TABLE / CREATE INDEX statements at startup."""
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SCHEMA_SQL)
        await conn.commit()
    log.info("schema ensured")
