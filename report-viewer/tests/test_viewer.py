"""Tests for /accessions and /rows."""

from __future__ import annotations

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
    fake_trino(["n"], [{"n": 2}])
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
