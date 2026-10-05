"""Test fixtures.

Postgres-bound tests look for `REPORT_VIEWER_TEST_DATABASE_URL` and are
skipped if it isn't set. Trino calls are always monkey-patched - we
test the API surface and the SQL we generate, not the live cluster.
"""

from __future__ import annotations

import base64
import os
import time

# Settings() resolves at import - provide a placeholder for required vars
# before the package gets loaded.
os.environ.setdefault("REPORT_VIEWER_EXTERNAL_URL", "http://testserver")

from typing import Any, Callable

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jose import jwt
from psycopg_pool import AsyncConnectionPool

from scout_report_viewer import db, jwks, trino_client
from scout_report_viewer.app import create_app
from scout_report_viewer.config import settings


TEST_DB_URL = os.environ.get("REPORT_VIEWER_TEST_DATABASE_URL", "")


class _FakeTrinoError(Exception):
    """Stub mirroring trino.exceptions.TrinoUserError's `error_name`."""

    def __init__(self, error_name: str | None = None) -> None:
        self.error_name = error_name
        super().__init__(error_name or "fake trino error")


def _needs_pg(request):
    if not TEST_DB_URL:
        pytest.skip(
            "REPORT_VIEWER_TEST_DATABASE_URL not set - Postgres-backed tests skipped"
        )


@pytest_asyncio.fixture
async def reset_schema():
    """Drop and recreate the `searches` and `plots` tables so each test starts
    clean."""
    _needs_pg(None)
    settings.database_url = TEST_DB_URL
    async with AsyncConnectionPool(TEST_DB_URL, open=False) as pool:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DROP TABLE IF EXISTS searches CASCADE")
                await cur.execute("DROP TABLE IF EXISTS plots CASCADE")
                await cur.execute("DROP TABLE IF EXISTS _yoyo_migration CASCADE")
                await cur.execute("DROP TABLE IF EXISTS _yoyo_log CASCADE")
                await cur.execute("DROP TABLE IF EXISTS _yoyo_version CASCADE")
                await cur.execute("DROP TABLE IF EXISTS yoyo_lock CASCADE")
            await conn.commit()
    await db.ensure_schema()
    yield


@pytest.fixture
def fake_trino(monkeypatch) -> Callable[[list[str], list[dict[str, Any]]], None]:
    """Stub out `trino_client.execute`. Returns a setter the test calls to
    enqueue the (columns, rows) the next Trino call should return.

    Tests can queue multiple responses so a flow like
    `create_search` -> `get_rows` -> `get_csv` primes a distinct payload
    for each Trino round-trip.

    Every (sql, params) round-trip is recorded on `enqueue.calls` so tests
    can assert on the SQL/params the service generated.
    """
    queue: list[Any] = []
    calls: list[tuple[str, list | tuple | None]] = []

    async def fake_execute(
        sql: str,
        user: str | None = None,
        params: list | tuple | None = None,
        progress_key: str | None = None,
        handle: object | None = None,
    ):
        calls.append((sql, params))
        if not queue:
            raise AssertionError(
                f"fake_trino had no queued response for SQL: {sql[:120]}..."
            )
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def fake_stream(
        sql: str,
        user: str | None = None,
        params: list | tuple | None = None,
        chunk_size: int = 1000,
    ):
        calls.append((sql, params))
        if not queue:
            raise AssertionError(
                f"fake_trino had no queued response for SQL: {sql[:120]}..."
            )
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        columns, rows = item
        yield columns, []
        for i in range(0, len(rows), chunk_size):
            yield columns, rows[i : i + chunk_size]

    monkeypatch.setattr(trino_client, "execute", fake_execute)
    monkeypatch.setattr(trino_client, "stream", fake_stream)

    def enqueue(columns: list[str], rows: list[dict[str, Any]]) -> None:
        queue.append((columns, rows))

    def enqueue_error(error_name: str | None = None) -> None:
        queue.append(_FakeTrinoError(error_name))

    enqueue.error = enqueue_error  # type: ignore[attr-defined]
    enqueue.calls = calls  # type: ignore[attr-defined]
    return enqueue


@pytest.fixture
def client(reset_schema):
    # Enter the context so the lifespan opens app.state.pool.
    with TestClient(create_app()) as c:
        yield c


# Shared RSA keypair + fake JWKS for every test that needs a real Bearer JWT
# (the only auth path since Path 2 - oauth2-proxy headers + gateway secret -
# was retired, issue #739). One signer for the whole suite so test_jwt_auth.py
# and anything else minting tokens agree on the same kid/keys.
TEST_KID = "test-key-1"
TEST_ISSUER = "http://test/realms/scout"


@pytest.fixture(scope="session")
def keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_numbers = priv.public_key().public_numbers()

    def _b64(n: int) -> str:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    jwk = {
        "kty": "RSA",
        "kid": TEST_KID,
        "alg": "RS256",
        "use": "sig",
        "n": _b64(pub_numbers.n),
        "e": _b64(pub_numbers.e),
    }
    return priv_pem, jwk


@pytest.fixture(autouse=True)
def install_test_jwks(keypair, monkeypatch):
    _, jwk = keypair

    class _StaticCache:
        def get_key(self, kid):
            return jwk if kid == TEST_KID else None

    monkeypatch.setattr(jwks, "get_default", lambda url: _StaticCache())
    monkeypatch.setattr(settings, "oidc_jwks_url", "http://test/jwks")
    monkeypatch.setattr(settings, "oidc_issuer", TEST_ISSUER)
    yield


def mint_token(priv_pem: bytes, **overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": "alice-keycloak-uuid",
        "preferred_username": "alice",
        "iss": TEST_ISSUER,
        "aud": settings.oidc_audience,
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return jwt.encode(claims, priv_pem, algorithm="RS256", headers={"kid": TEST_KID})


@pytest.fixture
def auth_headers(keypair) -> dict[str, str]:
    priv, _ = keypair
    token = mint_token(priv, sub="alice-keycloak-uuid", preferred_username="alice")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def other_auth_headers(keypair) -> dict[str, str]:
    """A second signed-in user, for cross-user access checks."""
    priv, _ = keypair
    token = mint_token(priv, sub="bob-keycloak-uuid", preferred_username="bob")
    return {"Authorization": f"Bearer {token}"}
