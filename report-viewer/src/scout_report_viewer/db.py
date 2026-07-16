"""Postgres access - psycopg3 connection pool + yoyo-driven migrations."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import psycopg
from psycopg_pool import AsyncConnectionPool
from yoyo import get_backend, read_migrations

from .config import settings

log = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


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


async def check_ready() -> None:
    """Raise if the DB pool can't serve a query fast. Backs /readyz."""
    pool = await open_pool()
    async with pool.connection(timeout=2.0) as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1")


def _apply_migrations_sync() -> None:
    backend = get_backend(settings.database_url)
    migrations = read_migrations(str(_MIGRATIONS_DIR))
    with backend.lock():
        pending = backend.to_apply(migrations)
        if not pending:
            log.info("schema up to date")
            return
        backend.apply_migrations(pending)
        log.info("applied %d migration(s)", len(pending))


async def ensure_schema() -> None:
    """Apply pending yoyo migrations. Blocking, so offloaded to a thread."""
    await asyncio.to_thread(_apply_migrations_sync)
