"""Postgres access - app-owned psycopg3 pool + yoyo-driven migrations.

The pool is created once in the lifespan and lives on app.state.pool;
handlers reach it via the get_pool / store dependencies.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import Request
from psycopg_pool import AsyncConnectionPool
from yoyo import get_backend, read_migrations

from .config import settings

log = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def create_pool() -> AsyncConnectionPool:
    """Open the pool. Non-blocking so the app still boots when Postgres is
    down; the pool self-heals and /readyz holds traffic until it can serve."""
    pool = AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=1,
        max_size=10,
        open=False,
    )
    await pool.open(wait=False)
    log.info("opened postgres pool")
    return pool


def get_pool(request: Request) -> AsyncConnectionPool:
    return request.app.state.pool


async def check_ready(pool: AsyncConnectionPool) -> None:
    """Raise if the pool can't serve a query fast. Backs /readyz."""
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
