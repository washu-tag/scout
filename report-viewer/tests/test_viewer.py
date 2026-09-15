"""Tests for /accessions and /rows."""

from __future__ import annotations

import asyncio

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
    """The `stats_callback` wiring into `conn.cursor()` is the fragile part:
    a client upgrade renaming the kwarg would silently kill every progress
    bar. Assert a real execute() publishes and then clears."""
    from scout_report_viewer import progress, trino_client

    mid_flight = {}

    class FakeCursor:
        def __init__(self, stats_callback=None, **_):
            self.description = [("n",)]
            self._cb = stats_callback

        def execute(self, sql, params=None):
            self._cb({"state": "RUNNING", "progressPercentage": 61.5})
            self._cb({"state": "RUNNING", "progressPercentage": 40.0})
            mid_flight.update(progress.get("k") or {})

        def fetchall(self):
            return [[1]]

    class FakeConn:
        def cursor(self, **kw):
            return FakeCursor(**kw)

        def close(self):
            pass

    monkeypatch.setattr(trino_client, "_new_conn", lambda user: FakeConn())
    asyncio.run(trino_client.execute("SELECT 1", user="alice", progress_key="k"))

    # Reported verbatim, including going backwards as Trino finds more splits.
    assert mid_flight == {"state": "RUNNING", "progressPercentage": 40.0}
    assert progress.get("k") is None


def test_progress_is_not_visible_to_another_user(client, auth_headers, fake_trino):
    from scout_report_viewer import progress

    dsid = _make_search(client, auth_headers, fake_trino)
    progress.report(f"search:{dsid}:alice", {"state": "RUNNING"})
    try:
        bob = {**auth_headers, "X-Auth-Request-Preferred-Username": "bob"}
        assert client.get(f"/api/searches/{dsid}/progress", headers=bob).json() == {}
        assert client.get(
            f"/api/searches/{dsid}/progress", headers=auth_headers
        ).json() == {"state": "RUNNING"}
    finally:
        progress.clear(f"search:{dsid}:alice")
