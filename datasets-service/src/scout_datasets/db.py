"""Postgres access — psycopg3 connection pool + the dataset schema.

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
-- Cohorts are saved SQL only. /rows wraps source_sql as a subquery
-- and applies pagination/sort/filter at the Trino layer on every read.
-- See ADR 0026 (Just-in-time cohort evaluation).
CREATE TABLE IF NOT EXISTS datasets (
  id              TEXT PRIMARY KEY,
  kind            TEXT NOT NULL,
  id_column       TEXT NOT NULL,
  source_sql      TEXT NOT NULL,
  sql_explanation TEXT NOT NULL DEFAULT '',
  highlight_terms TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  row_count       INTEGER,
  parent_id       TEXT REFERENCES datasets(id) ON DELETE SET NULL,
  owner_sub       TEXT NOT NULL,
  owui_chat_id    TEXT NOT NULL DEFAULT '',
  owui_chat_title TEXT NOT NULL DEFAULT '',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at      TIMESTAMPTZ NOT NULL,
  last_read_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS datasets_owner_expires_idx
  ON datasets (owner_sub, expires_at);

-- Adjust pre-existing `datasets` rows: drop materialization columns,
-- add the cached row_count + saved-cohort-related columns that the
-- CREATE TABLE above would have provided on a fresh install.
ALTER TABLE datasets DROP COLUMN IF EXISTS id_list;
ALTER TABLE datasets DROP COLUMN IF EXISTS row_metadata;
ALTER TABLE datasets DROP COLUMN IF EXISTS filter_chain;
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS row_count INTEGER;
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS source_sql TEXT NOT NULL DEFAULT '';
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS sql_explanation TEXT NOT NULL DEFAULT '';
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS highlight_terms TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS owui_chat_id TEXT NOT NULL DEFAULT '';
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS owui_chat_title TEXT NOT NULL DEFAULT '';
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
