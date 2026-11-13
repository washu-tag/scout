"""Query-result cache (#806): round trip, fail-open, and the routes serving
repeat reads without Trino."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from scout_report_viewer import cache
from scout_report_viewer.config import settings


class _FakeValkey:
    def __init__(self, fail: bool = False) -> None:
        self.data: dict[str, bytes] = {}
        self.ttls: dict[str, int] = {}
        self.fail = fail

    async def get(self, key: str) -> bytes | None:
        if self.fail:
            raise ConnectionError("valkey down")
        return self.data.get(key)

    async def set(self, key: str, value: bytes, ex: int) -> None:
        if self.fail:
            raise ConnectionError("valkey down")
        self.data[key] = value
        self.ttls[key] = ex


@pytest.fixture
def fake_valkey(monkeypatch) -> _FakeValkey:
    fake = _FakeValkey()
    monkeypatch.setattr(settings, "valkey_url", "redis://fake:6379")
    monkeypatch.setattr(settings, "cache_ttl_seconds", 600)
    monkeypatch.setattr(cache, "_client", fake)
    return fake


def test_round_trip_compresses_and_sets_ttl(fake_valkey):
    columns = ["accession_number", "modality"]
    rows = [{"accession_number": f"A{i}", "modality": "CT"} for i in range(500)]
    value = {"columns": columns, "rows": cache.pack_rows(columns, rows)}
    key = cache.key("rows", "alice", "SELECT 1", None, 10)

    asyncio.run(cache.put(key, value))
    got = asyncio.run(cache.get("rows", key))

    assert cache.unpack_rows(got["columns"], got["rows"]) == rows
    assert fake_valkey.ttls[key] == 600
    assert len(fake_valkey.data[key]) < len(str(value)) / 5


def test_key_separates_users_sql_and_params():
    base = cache.key("rows", "alice", "SELECT 1", ["A1"], 10)
    assert base.startswith("rv:v1:rows:")
    assert base != cache.key("rows", "bob", "SELECT 1", ["A1"], 10)
    assert base != cache.key("rows", "alice", "SELECT 2", ["A1"], 10)
    assert base != cache.key("rows", "alice", "SELECT 1", ["A2"], 10)
    assert base != cache.key("plot", "alice", "SELECT 1", ["A1"], 10)


def test_off_without_url_or_with_zero_ttl(monkeypatch):
    monkeypatch.setattr(cache, "_client", _FakeValkey())
    monkeypatch.setattr(settings, "valkey_url", "")
    assert asyncio.run(cache.get("rows", "k")) is None

    monkeypatch.setattr(settings, "valkey_url", "redis://fake:6379")
    monkeypatch.setattr(settings, "cache_ttl_seconds", 0)
    asyncio.run(cache.put("k", [1]))
    assert asyncio.run(cache.get("rows", "k")) is None


def test_valkey_failure_reads_as_a_miss(fake_valkey):
    fake_valkey.fail = True
    asyncio.run(cache.put("k", [1]))
    assert asyncio.run(cache.get("rows", "k")) is None


def test_corrupt_entry_reads_as_a_miss(fake_valkey):
    fake_valkey.data["k"] = b"not zlib"
    assert asyncio.run(cache.get("rows", "k")) is None


def _columns() -> list[str]:
    return ["primary_report_identifier", "accession_number", "modality", "report_text"]


def _rows() -> list[dict[str, Any]]:
    return [
        {
            "primary_report_identifier": f"r{i}",
            "accession_number": f"A{i}",
            "modality": "CT",
            "report_text": "body",
        }
        for i in range(3)
    ]


def test_rows_and_accessions_repeat_without_trino(
    client, auth_headers, fake_trino, fake_valkey
):
    fake_trino(_columns(), _rows())
    created = client.post(
        "/api/searches",
        json={"sql": "SELECT * FROM reports_latest WHERE modality = 'CT'"},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    sid = created.json()["id"]

    fake_trino(_columns(), _rows())
    first = client.get(f"/api/searches/{sid}/rows", headers=auth_headers)
    assert first.status_code == 200, first.text
    calls = len(fake_trino.calls)

    second = client.get(f"/api/searches/{sid}/rows", headers=auth_headers)
    assert second.status_code == 200, second.text
    assert len(fake_trino.calls) == calls
    assert second.json() == first.json()
    assert "report_text" not in second.json()["columns"]
    assert all(b"body" not in v for v in fake_valkey.data.values())

    fake_trino(["accession_number"], [{"accession_number": "A1"}])
    first = client.get(f"/api/searches/{sid}/accessions", headers=auth_headers)
    calls = len(fake_trino.calls)
    second = client.get(f"/api/searches/{sid}/accessions", headers=auth_headers)
    assert len(fake_trino.calls) == calls
    assert second.json() == first.json() == {"search_id": sid, "accessions": ["A1"]}
