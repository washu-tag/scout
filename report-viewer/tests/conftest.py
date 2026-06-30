"""Test fixtures.

Postgres-bound tests look for `REPORT_VIEWER_TEST_DATABASE_URL` and are
skipped if it isn't set. Trino calls are always monkey-patched — we
test the API surface and the SQL we generate, not the live cluster.
"""

from __future__ import annotations

import os

# Settings() resolves at import — provide a placeholder for required vars
# before the package gets loaded.
os.environ.setdefault("REPORT_VIEWER_EXTERNAL_URL", "http://testserver")

from typing import Any, Callable

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from scout_report_viewer import db, trino_client
from scout_report_viewer.app import create_app
from scout_report_viewer.config import settings


TEST_DB_URL = os.environ.get("REPORT_VIEWER_TEST_DATABASE_URL", "")


def _needs_pg(request):
    if not TEST_DB_URL:
        pytest.skip(
            "REPORT_VIEWER_TEST_DATABASE_URL not set — Postgres-backed tests skipped"
        )


@pytest_asyncio.fixture
async def reset_schema():
    """Drop and recreate the `searches` table so each test starts clean."""
    _needs_pg(None)
    settings.database_url = TEST_DB_URL
    await db.close_pool()
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DROP TABLE IF EXISTS searches CASCADE")
        await conn.commit()
    await db.ensure_schema()
    yield
    await db.close_pool()


@pytest.fixture
def fake_trino(monkeypatch) -> Callable[[list[str], list[dict[str, Any]]], None]:
    """Stub out `trino_client.execute`. Returns a setter the test calls to
    enqueue the (columns, rows) the next Trino call should return.

    We support multiple queued responses so a test exercising
    `create_search` -> `get_rows` -> `get_summary` can prime distinct
    payloads for each Trino round-trip.
    """
    queue: list[tuple[list[str], list[dict[str, Any]]]] = []

    async def fake_execute(
        sql: str,
        user: str | None = None,
        params: list | tuple | None = None,
    ):
        if not queue:
            raise AssertionError(
                f"fake_trino had no queued response for SQL: {sql[:120]}..."
            )
        return queue.pop(0)

    monkeypatch.setattr(trino_client, "execute", fake_execute)

    def enqueue(columns: list[str], rows: list[dict[str, Any]]) -> None:
        queue.append((columns, rows))

    return enqueue


@pytest.fixture
def client(reset_schema) -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-Auth-Request-Preferred-Username": "alice"}
