"""Query-result cache (#806): keys, the settle check, the data-version refresh,
failing open, and the routes serving repeat reads without Trino."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import OrderedDict
from typing import Any

import pytest

from scout_report_viewer import cache, trino_client
from scout_report_viewer.config import settings
from tests.test_actions import (  # noqa: F401 (fixture)
    _FakeAsyncClient,
    _bearer_headers,
    _create_search,
    catalog_with_backend_call_action,
)


class _FakeValkey:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.ttls: dict[str, int] = {}
        self.fail = False

    async def get(self, key: str) -> bytes | None:
        if self.fail:
            raise ConnectionError("valkey down")
        return self.data.get(key)

    async def set(self, key: str, value: bytes, ex: int) -> None:
        if self.fail:
            raise ConnectionError("valkey down")
        self.data[key] = value
        self.ttls[key] = ex

    async def aclose(self) -> None:
        pass


class _Env:
    def __init__(self, valkey: _FakeValkey) -> None:
        self.valkey = valkey
        self.fingerprints: dict[str, str | None] = {"alice": "fp-a", "bob": "fp-b"}
        self.version: str | None = "v1"

    async def fingerprint(self, user: str) -> str | None:
        return self.fingerprints.get(user, f"fp-{user}")

    def data_version(self, user: str, sql: str) -> str | None:
        return self.version


@pytest.fixture
def env(monkeypatch) -> _Env:
    e = _Env(_FakeValkey())
    monkeypatch.setattr(settings, "valkey_url", "redis://fake:6379")
    monkeypatch.setattr(settings, "opa_url", "http://opa:8181")
    monkeypatch.setattr(settings, "cache_ttl_seconds", 600)
    monkeypatch.setattr(settings, "cache_settle_seconds", 0)
    monkeypatch.setattr(cache, "_valkey", e.valkey)
    monkeypatch.setattr(cache, "_fingerprint", e.fingerprint)
    monkeypatch.setattr(cache, "_data_version", e.data_version)
    return e


async def _store(op: str, user: str, value: Any, *parts: Any) -> cache.Lookup:
    found = await cache.lookup(op, user, *parts)
    cache.remember(found, value)
    await cache.wait_pending()
    return found


def test_round_trip_compresses_and_sets_ttl(env):
    columns = ["accession_number", "modality"]
    rows = [{"accession_number": f"A{i}", "modality": "CT"} for i in range(500)]
    value = {"columns": columns, "rows": cache.pack_rows(columns, rows)}

    async def go():
        first = await _store("rows", "alice", value, "SELECT 1", None, 10)
        assert first.value is None
        return first, await cache.lookup("rows", "alice", "SELECT 1", None, 10)

    first, second = asyncio.run(go())
    assert cache.unpack_rows(second.value["columns"], second.value["rows"]) == rows
    assert env.valkey.ttls[first.key] == 600
    assert first.key.startswith("rv:v1:rows:")
    assert len(env.valkey.data[first.key]) < len(str(value)) / 5


def test_key_covers_user_query_fingerprint_and_version(env, monkeypatch):
    async def key(user="alice", sql="SELECT 1") -> str:
        return (await cache.lookup("rows", user, sql, None, 10)).key

    base = asyncio.run(key())
    assert base != asyncio.run(key(user="bob"))
    assert base != asyncio.run(key(sql="SELECT 2"))
    env.fingerprints["alice"] = "fp-a2"
    assert base != asyncio.run(key())
    env.fingerprints["alice"] = "fp-a"
    env.version = "v2"
    assert base != asyncio.run(key())


def test_not_stored_when_the_fingerprint_changes_before_the_settle_check(env):
    async def go():
        found = await cache.lookup("rows", "alice", "SELECT 1")
        env.fingerprints["alice"] = "fp-a2"
        cache.remember(found, [1])
        await cache.wait_pending()

    asyncio.run(go())
    assert env.valkey.data == {}


def test_stored_only_after_the_settle_window(env, monkeypatch):
    monkeypatch.setattr(settings, "cache_settle_seconds", 0.3)

    async def go():
        found = await cache.lookup("rows", "alice", "SELECT 1")
        cache.remember(found, [1])
        await asyncio.sleep(0.05)
        early = dict(env.valkey.data)
        await cache.wait_pending()
        return found, early, time.monotonic() - found.started

    found, early, elapsed = asyncio.run(go())
    assert early == {}
    assert found.key in env.valkey.data
    assert elapsed >= 0.3


def test_off_without_urls_or_with_zero_ttl(env, monkeypatch):
    for setting, value in [
        ("valkey_url", ""),
        ("opa_url", ""),
        ("cache_ttl_seconds", 0),
    ]:
        with monkeypatch.context() as m:
            m.setattr(settings, setting, value)
            found = asyncio.run(cache.lookup("rows", "alice", "SELECT 1"))
            assert found.key is None
            cache.remember(found, [1])
    assert env.valkey.data == {}


def test_uncached_when_the_fingerprint_is_unavailable(env):
    env.fingerprints["alice"] = None
    found = asyncio.run(cache.lookup("rows", "alice", "SELECT 1"))
    assert found.key is None


class _FakeDelta:
    """Trino's answers to the table and version lookups: a plan naming each
    query's input tables, and each table's latest Delta version."""

    def __init__(self) -> None:
        self.inputs: dict[str, list[tuple[str, str, str]]] = {}
        self.versions: dict[str, int] = {}
        self.calls: list[str] = []
        self.fail = False
        self.listed: list[str] = []
        self.views: list[str] = []
        self.filtered: set[str] = set()
        self.users: list[str | None] = []

    async def execute(self, sql: str, user: str | None = None, **_: Any):
        self.calls.append(sql)
        self.users.append(user)
        if self.fail:
            raise RuntimeError("trino down")
        if sql.startswith("EXPLAIN"):
            query = sql.removeprefix("EXPLAIN (TYPE IO, FORMAT JSON) ")
            infos = [
                {"table": {"catalog": c, "schemaTable": {"schema": s, "table": t}}}
                for c, s, t in self.inputs[query]
            ]
            plan = json.dumps({"inputTableColumnInfos": infos})
            return ["Query Plan"], [{"Query Plan": plan}]
        if "information_schema.tables" in sql:
            return ["table_name"], [{"table_name": t} for t in self.listed]
        if "information_schema.views" in sql:
            return ["view_definition"], [{"view_definition": v} for v in self.views]
        rows = [
            {
                "i": int(i),
                "version": None if t in self.filtered else self.versions[t],
                "ts": None if t in self.filtered else "2026-11-01",
            }
            for i, t in re.findall(r'SELECT (\d+) AS i,.*?"([^"]+)\$history"', sql)
        ]
        return ["i", "version", "ts"], rows


@pytest.fixture
def delta(monkeypatch) -> _FakeDelta:
    d = _FakeDelta()
    monkeypatch.setattr(trino_client, "execute", d.execute)
    monkeypatch.setattr(cache, "_tables", OrderedDict())
    monkeypatch.setattr(cache, "_resolving", {})
    monkeypatch.setattr(cache, "_versions", {})
    monkeypatch.setattr(cache, "_checked", {})
    monkeypatch.setattr(cache, "_refreshing", set())
    return d


_VIEW_SQL = "SELECT * FROM reports_latest_epic_view"
_VIEW_INPUTS = [
    ("delta", "default", "reports_latest"),
    ("delta", "default", "reports_report_patient_mapping"),
]


def _version(sql: str) -> str | None:
    """The data version once the background lookups have had their chance."""

    async def go():
        cold = sql not in cache._tables
        if cache._data_version("alice", sql) is not None:
            assert not cold, "a query never seen runs uncached"
        await cache.wait_pending()
        cache._data_version("alice", sql)
        await cache.wait_pending()
        return cache._data_version("alice", sql)

    return asyncio.run(go())


def test_data_version_covers_every_input_table(delta):
    delta.inputs[_VIEW_SQL] = _VIEW_INPUTS
    delta.versions = {"reports_latest": 2, "reports_report_patient_mapping": 4}
    first = _version(_VIEW_SQL)
    assert first is not None
    explains = [c for c in delta.calls if c.startswith("EXPLAIN")]
    histories = [c for c in delta.calls if "$history" in c]
    assert len(explains) == 1 and len(histories) == 1
    assert '"reports_latest$history"' in histories[0]
    assert '"reports_report_patient_mapping$history"' in histories[0]
    assert delta.users[delta.calls.index(histories[0])] is None, "as Report Viewer"

    delta.versions["reports_report_patient_mapping"] = 5
    assert _version(_VIEW_SQL) == first, "re-read only once the refresh is due"
    cache._checked.clear()
    assert _version(_VIEW_SQL) != first


def test_data_version_none_when_an_input_is_outside_delta(delta):
    delta.inputs[_VIEW_SQL] = [*_VIEW_INPUTS, ("hive", "default", "extra")]
    assert _version(_VIEW_SQL) is None
    assert not any("$history" in c for c in delta.calls)


def test_failed_version_refresh_keeps_the_last_value_and_waits(delta):
    delta.inputs[_VIEW_SQL] = _VIEW_INPUTS
    delta.versions = {"reports_latest": 2, "reports_report_patient_mapping": 4}
    first = _version(_VIEW_SQL)
    cache._checked.clear()
    delta.fail = True
    calls = len(delta.calls)
    assert _version(_VIEW_SQL) == first
    assert len(delta.calls) == calls + 1, "one retry per refresh interval"


def test_failed_table_lookup_waits_before_retrying(delta):
    delta.fail = True
    assert _version(_VIEW_SQL) is None
    assert len(delta.calls) == 1


def test_explain_replaces_only_bound_parameters():
    sql = "SELECT '?' AS q FROM t WHERE contains(?, a) -- ?"
    assert cache._explainable(sql) == (
        "SELECT '?' AS q FROM t WHERE "
        "contains(CAST(ARRAY[] AS array(varchar)), a) -- ?"
    )


def test_valkey_failure_reads_as_a_miss_and_skips_the_write(env):
    env.valkey.fail = True
    found = asyncio.run(_store("rows", "alice", [1], "SELECT 1"))
    assert found.key is not None
    assert found.value is None


def test_corrupt_entry_reads_as_a_miss(env):
    found = asyncio.run(cache.lookup("rows", "alice", "SELECT 1"))
    env.valkey.data[found.key] = b"not zlib"
    assert asyncio.run(cache.lookup("rows", "alice", "SELECT 1")).value is None


def test_is_user_error():
    class E(Exception):
        def __init__(self, error_type: str) -> None:
            self.error_type = error_type

    assert trino_client.is_user_error(E("USER_ERROR"))
    assert not trino_client.is_user_error(E("INSUFFICIENT_RESOURCES"))
    assert not trino_client.is_user_error(RuntimeError())


# --- Routes (Postgres-backed) -------------------------------------------------


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


_SQL_SCORED = (
    "SELECT primary_report_identifier, accession_number, modality FROM reports_latest "
    "WHERE REGEXP_LIKE(COALESCE(report_section_impression, ''), '(?is)stroke')"
)


def _create(client, auth_headers, fake_trino, sql: str) -> str:
    fake_trino(_columns(), _rows())
    created = client.post("/api/searches", json={"sql": sql}, headers=auth_headers)
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_rows_and_accessions_repeat_without_trino(
    client, auth_headers, fake_trino, env
):
    sid = _create(
        client, auth_headers, fake_trino, "SELECT * FROM reports_latest WHERE 1=1"
    )

    fake_trino(_columns(), _rows())
    first = client.get(f"/api/searches/{sid}/rows", headers=auth_headers)
    assert first.status_code == 200, first.text
    client.portal.call(cache.wait_pending)
    calls = len(fake_trino.calls)

    second = client.get(f"/api/searches/{sid}/rows", headers=auth_headers)
    assert second.status_code == 200, second.text
    assert len(fake_trino.calls) == calls
    assert second.json() == first.json()
    assert "report_text" not in second.json()["columns"]

    fake_trino(["accession_number"], [{"accession_number": "A1"}])
    first = client.get(f"/api/searches/{sid}/accessions", headers=auth_headers)
    client.portal.call(cache.wait_pending)
    calls = len(fake_trino.calls)
    second = client.get(f"/api/searches/{sid}/accessions", headers=auth_headers)
    assert len(fake_trino.calls) == calls
    assert second.json() == first.json() == {"search_id": sid, "accessions": ["A1"]}


def test_rows_skip_caching_after_a_transient_rewrite_failure(
    client, auth_headers, fake_trino, env
):
    sid = _create(client, auth_headers, fake_trino, _SQL_SCORED)
    fake_trino.error("EXCEEDED_TIME_LIMIT", "INSUFFICIENT_RESOURCES")
    fake_trino(_columns(), _rows())
    r = client.get(f"/api/searches/{sid}/rows", headers=auth_headers)
    assert r.status_code == 200, r.text
    client.portal.call(cache.wait_pending)
    assert not [k for k in env.valkey.data if k.startswith("rv:v1:rows:")]


def test_rows_cache_the_fallback_when_trino_rejects_the_rewrite(
    client, auth_headers, fake_trino, env
):
    sid = _create(client, auth_headers, fake_trino, _SQL_SCORED)
    fake_trino.error("COLUMN_NOT_FOUND", "USER_ERROR")
    fake_trino(_columns(), _rows())
    r = client.get(f"/api/searches/{sid}/rows", headers=auth_headers)
    assert r.status_code == 200, r.text
    client.portal.call(cache.wait_pending)
    calls = len(fake_trino.calls)
    again = client.get(f"/api/searches/{sid}/rows", headers=auth_headers)
    assert len(fake_trino.calls) == calls
    assert again.json() == r.json()


# --- Action clicks -------------------------------------------------------------


_COHORT = [
    {"primary_report_identifier": "s3://x/1", "accession_number": "ACC1"},
    {"primary_report_identifier": "s3://x/2", "accession_number": None},
]


def _invoke(client, headers, sid, visible=None):
    r = client.post(
        f"/api/searches/{sid}/actions/explore-xnat-demo/invoke",
        headers=headers,
        json={"visible_report_ids": visible},
    )
    assert r.status_code == 200, r.text
    return r.json(), _FakeAsyncClient.last_call["json"]


def test_action_click_reads_the_pages_cohort(
    client, keypair, fake_trino, env, catalog_with_backend_call_action
):
    headers = _bearer_headers(keypair[0])
    sid = _create_search(client, headers, fake_trino)
    fake_trino(["primary_report_identifier", "accession_number"], _COHORT)
    assert client.get(f"/api/searches/{sid}/rows", headers=headers).status_code == 200
    client.portal.call(cache.wait_pending)
    calls = len(fake_trino.calls)

    body, sent = _invoke(client, headers, sid, ["s3://x/2", "s3://x/9"])
    assert len(fake_trino.calls) == calls
    assert sent["reports"] == [_COHORT[1]]
    assert body["dropped_report_count"] == 1


def test_action_click_reads_brief_ids_before_the_full_entry_lands(
    client, keypair, fake_trino, env, catalog_with_backend_call_action, monkeypatch
):
    monkeypatch.setattr(cache, "remember", lambda *_: None)
    headers = _bearer_headers(keypair[0])
    sid = _create_search(client, headers, fake_trino)
    fake_trino(["primary_report_identifier", "accession_number"], _COHORT)
    client.get(f"/api/searches/{sid}/rows", headers=headers)
    client.portal.call(cache.wait_pending)
    calls = len(fake_trino.calls)

    (key,) = env.valkey.data
    assert key.startswith("rv:v1:rows-ids:")
    assert env.valkey.ttls[key] == settings.cache_recent_seconds
    _body, sent = _invoke(client, headers, sid)
    assert len(fake_trino.calls) == calls
    assert sent["reports"] == _COHORT

    fake_trino(["primary_report_identifier", "accession_number"], _COHORT)
    client.get(f"/api/searches/{sid}/rows", headers=headers)
    assert len(fake_trino.calls) == calls + 1, "a page load never reads them"


def test_action_click_on_a_miss_runs_the_pages_query(
    client, keypair, fake_trino, env, catalog_with_backend_call_action
):
    headers = _bearer_headers(keypair[0])
    sid = _create_search(client, headers, fake_trino)
    fake_trino(["primary_report_identifier", "accession_number"], _COHORT)
    _body, sent = _invoke(client, headers, sid)
    client.portal.call(cache.wait_pending)
    assert sent["reports"] == _COHORT

    calls = len(fake_trino.calls)
    r = client.get(f"/api/searches/{sid}/rows", headers=headers)
    assert len(fake_trino.calls) == calls, "the click's result serves the page"
    assert r.json()["rows"] == _COHORT


# --- First-run caching -----------------------------------------------------------


@pytest.fixture
def live(monkeypatch, delta) -> _FakeValkey:
    """The cache with real table and version lookups against `delta`."""
    valkey = _FakeValkey()
    monkeypatch.setattr(settings, "valkey_url", "redis://fake:6379")
    monkeypatch.setattr(settings, "opa_url", "http://opa:8181")
    monkeypatch.setattr(settings, "cache_ttl_seconds", 600)
    monkeypatch.setattr(settings, "cache_settle_seconds", 0)
    monkeypatch.setattr(cache, "_valkey", valkey)

    async def fingerprint(user: str) -> str:
        return f"fp-{user}"

    monkeypatch.setattr(cache, "_fingerprint", fingerprint)
    return valkey


def _known(delta: _FakeDelta, **versions: int) -> None:
    """Versions already read, as by the startup warm-up."""
    now = time.monotonic()
    for name, v in versions.items():
        delta.versions[name] = v
        cache._versions[("delta", "default", name)] = f"{v}:2026-11-01"
        cache._checked[("delta", "default", name)] = now


def test_first_run_is_cached_when_its_tables_versions_are_known(live, delta):
    delta.inputs[_VIEW_SQL] = _VIEW_INPUTS
    _known(delta, reports_latest=2, reports_report_patient_mapping=4)

    async def go():
        cold = await cache.lookup("rows", "alice", _VIEW_SQL, 10)
        cache.remember(cold, [1])
        cache.remember_briefly(cold, "rows-ids", [2], 60)
        await cache.wait_pending()
        return cold, await cache.lookup("rows", "alice", _VIEW_SQL, 10)

    cold, warm = asyncio.run(go())
    assert cold.digest is None and cold.value is None
    assert warm.value == [1]
    assert live.ttls[warm.key_for("rows-ids")] == 60
    assert not any("$history" in c for c in delta.calls)


def test_first_run_not_stored_when_a_tables_version_was_unknown(live, delta):
    delta.inputs[_VIEW_SQL] = _VIEW_INPUTS
    _known(delta, reports_latest=2)
    delta.versions["reports_report_patient_mapping"] = 4

    async def go():
        cold = await cache.lookup("rows", "alice", _VIEW_SQL)
        cache.remember(cold, [1])
        await cache.wait_pending()
        return await cache.lookup("rows", "alice", _VIEW_SQL)

    second = asyncio.run(go())
    assert live.data == {}
    assert second.digest is not None, "the missing version was read after EXPLAIN"
    assert second.value is None


def test_filtered_history_is_never_recorded_as_a_version(delta):
    delta.inputs[_VIEW_SQL] = _VIEW_INPUTS
    delta.versions = {"reports_latest": 2}
    delta.filtered = {"reports_report_patient_mapping"}
    assert _version(_VIEW_SQL) is None
    assert ("delta", "default", "reports_report_patient_mapping") not in cache._versions


def test_warm_up_reads_every_table_and_skips_ones_without_history(live, delta):
    delta.listed = ["reports_latest", "reports_latest_spark_epic_view"]
    delta.views = [
        "WITH p AS (SELECT * FROM reports_report_patient_mapping) "
        "SELECT r.* FROM reports_latest r JOIN p ON true"
    ]
    delta.versions = {"reports_latest": 3, "reports_report_patient_mapping": 5}

    async def go():
        cache.start_warm_up()
        assert not cache.warmed_up()
        await cache.wait_pending()
        return cache.warmed_up()

    assert asyncio.run(go())
    assert cache._versions == {
        ("delta", "default", "reports_latest"): "3:2026-11-01",
        ("delta", "default", "reports_report_patient_mapping"): "5:2026-11-01",
    }
    assert set(delta.users) == {None}


def test_warm_up_stops_holding_readiness_after_its_limit(monkeypatch):
    class Running:
        def done(self) -> bool:
            return False

    monkeypatch.setattr(cache, "_warm_up", Running())
    monkeypatch.setattr(settings, "cache_warm_up_seconds", 30)
    monkeypatch.setattr(cache, "_warm_up_started", time.monotonic())
    assert not cache.warmed_up()
    monkeypatch.setattr(cache, "_warm_up_started", time.monotonic() - 31)
    assert cache.warmed_up()


def test_readyz_waits_for_the_warm_up(client, monkeypatch):
    monkeypatch.setattr(cache, "warmed_up", lambda: False)
    assert client.get("/readyz").status_code == 503
    monkeypatch.setattr(cache, "warmed_up", lambda: True)
    assert client.get("/readyz").status_code == 200
