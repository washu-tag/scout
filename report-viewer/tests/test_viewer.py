"""Tests for /accessions and /rows."""

from __future__ import annotations

import asyncio
import threading

import pytest

from scout_report_viewer.config import settings


_SQL = "SELECT primary_report_identifier, accession_number FROM reports_latest"
_SAMPLE_COLS = ["primary_report_identifier", "accession_number"]


def _sample_rows(n: int) -> list[dict]:
    return [
        {"primary_report_identifier": f"s3://bucket/{i}", "accession_number": f"ACC{i}"}
        for i in range(n)
    ]


def _make_search(client, auth_headers, fake_trino) -> str:
    fake_trino(_SAMPLE_COLS, _sample_rows(2))
    return client.post(
        "/api/searches", json={"sql": _SQL}, headers=auth_headers
    ).json()["id"]


def test_accessions_returns_deduped_list(client, auth_headers, fake_trino):
    dsid = _make_search(client, auth_headers, fake_trino)
    fake_trino(
        ["accession_number"],
        [{"accession_number": "ACC100"}, {"accession_number": "ACC200"}],
    )
    r = client.get(f"/api/searches/{dsid}/accessions", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["accessions"] == ["ACC100", "ACC200"]


def test_rows_strips_report_bodies(client, auth_headers, fake_trino):
    dsid = _make_search(client, auth_headers, fake_trino)
    fake_trino(
        [
            "primary_report_identifier",
            "accession_number",
            "report_text",
            "report_section_impression",
        ],
        [
            {
                "primary_report_identifier": "s3://bucket/1",
                "accession_number": "ACC1",
                "report_text": "a very long report body",
                "report_section_impression": "impression",
            },
        ],
    )
    r = client.get(f"/api/searches/{dsid}/rows", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["columns"] == ["primary_report_identifier", "accession_number"]
    assert body["rows"] == [
        {"primary_report_identifier": "s3://bucket/1", "accession_number": "ACC1"}
    ]
    assert body["total"] == 1
    assert body["truncated"] is False


def test_rows_truncates_at_cap(client, auth_headers, fake_trino, monkeypatch):
    dsid = _make_search(client, auth_headers, fake_trino)
    monkeypatch.setattr(settings, "max_cohort_rows", 3, raising=False)
    # Endpoint fetches cap+1 (=4) to detect overflow; return 4 rows.
    fake_trino(_SAMPLE_COLS, _sample_rows(4))
    r = client.get(f"/api/searches/{dsid}/rows", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is True
    assert body["total"] == 3
    assert len(body["rows"]) == 3
    assert "LIMIT 4" in fake_trino.calls[-1][0]


def test_trino_stats_reach_the_progress_store(monkeypatch):
    """A client upgrade renaming the `stats_callback` kwarg would silently
    kill every loading indicator, so drive the real execute() path."""
    from scout_report_viewer import progress, trino_client

    mid_flight = {}

    class FakeCursor:
        def __init__(self, stats_callback=None, **_):
            self.description = [("n",)]
            self._cb = stats_callback

        def execute(self, sql, params=None):
            self._cb({"state": "RUNNING", "processedRows": 1000})
            self._cb({"state": "RUNNING", "processedRows": 5000})
            mid_flight.update(progress.get("k") or {})

        def fetchall(self):
            return [[1]]

        def cancel(self):
            pass

    class FakeConn:
        def cursor(self, **kw):
            return FakeCursor(**kw)

        def close(self):
            pass

    monkeypatch.setattr(trino_client, "_new_conn", lambda user: FakeConn())
    asyncio.run(trino_client.execute("SELECT 1", user="alice", progress_key="k"))

    assert mid_flight == {"state": "RUNNING", "processedRows": 5000}
    # Kept after the query returns, flagged, so the SPA can read final totals.
    assert progress.get("k") == {
        "state": "RUNNING",
        "processedRows": 5000,
        "done": True,
    }


def test_progress_is_not_visible_to_another_user(client, auth_headers, fake_trino):
    from scout_report_viewer import progress

    dsid = _make_search(client, auth_headers, fake_trino)
    key = f"search:{dsid}:alice:tok1"
    progress.report(key, {"state": "RUNNING"})
    try:
        bob = {**auth_headers, "X-Auth-Request-Preferred-Username": "bob"}
        url = f"/api/searches/{dsid}/progress?progress_id=tok1"
        assert client.get(url, headers=bob).json() == {}
        assert client.get(url, headers=auth_headers).json() == {"state": "RUNNING"}
    finally:
        progress.finish(key)


def _fake_trino_conn(monkeypatch, cursor_cls):
    from scout_report_viewer import trino_client

    class FakeConn:
        def cursor(self, **kw):
            return cursor_cls(**kw)

        def close(self):
            pass

    monkeypatch.setattr(trino_client, "_new_conn", lambda user: FakeConn())


def test_a_disconnected_client_cancels_the_query(monkeypatch):
    """The worker thread cannot be interrupted, so an abandoned scan only ends
    if the cursor is cancelled at Trino."""
    from scout_report_viewer import trino_client

    started = threading.Event()
    cancelled = threading.Event()

    class FakeCursor:
        def __init__(self, **_):
            self.description = [("n",)]

        def execute(self, sql, params=None):
            started.set()
            cancelled.wait(5)

        def fetchall(self):
            raise RuntimeError("query was cancelled")

        def cancel(self):
            cancelled.set()

    _fake_trino_conn(monkeypatch, FakeCursor)

    async def scenario():
        async def receive():
            await asyncio.to_thread(started.wait, 5)
            return {"type": "http.disconnect"}

        handle = trino_client.QueryHandle()
        work = trino_client.execute("SELECT 1", user="alice", handle=handle)
        with pytest.raises(trino_client.ClientDisconnected):
            await trino_client.cancel_on_disconnect(receive, work, handle)

    asyncio.run(scenario())
    assert cancelled.is_set()


def test_a_finished_query_is_not_cancelled(monkeypatch):
    from scout_report_viewer import trino_client

    cancels = []

    class FakeCursor:
        def __init__(self, **_):
            self.description = [("n",)]

        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return [[1]]

        def cancel(self):
            cancels.append(True)

    _fake_trino_conn(monkeypatch, FakeCursor)

    async def scenario():
        async def receive():
            # A live client sends nothing more on a GET.
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        handle = trino_client.QueryHandle()
        work = trino_client.execute("SELECT 1", user="alice", handle=handle)
        return await trino_client.cancel_on_disconnect(receive, work, handle)

    columns, rows = asyncio.run(scenario())
    assert rows == [{"n": 1}]
    assert cancels == []


def test_a_cancel_that_beats_the_cursor_skips_the_query(monkeypatch):
    """The cursor is created on a worker thread, so a disconnect can land
    first. Cancelling a cursor with no query id yet does nothing, so the query
    must not be started at all."""
    from scout_report_viewer import trino_client

    executed = []

    class FakeCursor:
        def __init__(self, **_):
            self.description = [("n",)]

        def execute(self, sql, params=None):
            executed.append(sql)

        def fetchall(self):
            return [[1]]

        def cancel(self):
            pass

    _fake_trino_conn(monkeypatch, FakeCursor)
    handle = trino_client.QueryHandle()
    handle.cancel()

    with pytest.raises(trino_client.ClientDisconnected):
        asyncio.run(trino_client.execute("SELECT 1", user="alice", handle=handle))

    assert executed == []


def test_handles_do_not_share_cancel_state():
    """Two requests for the same search run concurrently after Cancel then
    Retry, so cancelling one must not touch the other."""
    from scout_report_viewer import trino_client

    first, second = [], []
    a, b = trino_client.QueryHandle(), trino_client.QueryHandle()
    a.arm(lambda: first.append(True))
    b.arm(lambda: second.append(True))

    a.cancel()

    assert first == [True]
    assert second == []


def test_progress_is_scoped_to_one_attempt(client, auth_headers, fake_trino):
    """A retry must not read the stats of the attempt it replaced."""
    from scout_report_viewer import progress

    dsid = _make_search(client, auth_headers, fake_trino)
    old_key = f"search:{dsid}:alice:old"
    new_key = f"search:{dsid}:alice:new"
    progress.report(old_key, {"state": "RUNNING", "processedRows": 10})
    progress.report(new_key, {"state": "RUNNING", "processedRows": 20})
    try:
        base = f"/api/searches/{dsid}/progress"
        assert client.get(f"{base}?progress_id=new", headers=auth_headers).json() == {
            "state": "RUNNING",
            "processedRows": 20,
        }
        # A token the store has never seen reports nothing, rather than someone
        # else's query.
        assert client.get(f"{base}?progress_id=zzz", headers=auth_headers).json() == {}
        assert client.get(base, headers=auth_headers).json() == {}
        assert client.get(f"{base}?progress_id=bad!", headers=auth_headers).json() == {}
    finally:
        progress.finish(old_key)
        progress.finish(new_key)
