"""Tests for /accessions and /csv."""

from __future__ import annotations


_SQL = "SELECT primary_report_identifier, accession_number FROM reports_latest"
_SAMPLE_COLS = ["primary_report_identifier", "accession_number"]


def _sample_rows(n: int) -> list[dict]:
    return [
        {"primary_report_identifier": f"s3://bucket/{i}", "accession_number": f"ACC{i}"}
        for i in range(n)
    ]


def test_accessions_returns_deduped_list(client, auth_headers, fake_trino):
    fake_trino(_SAMPLE_COLS, _sample_rows(3))
    fake_trino(["n"], [{"n": 3}])
    dsid = client.post(
        "/api/searches",
        json={"sql": _SQL},
        headers=auth_headers,
    ).json()["id"]

    fake_trino(
        ["accession_number"],
        [{"accession_number": "ACC100"}, {"accession_number": "ACC200"}],
    )
    r = client.get(f"/api/searches/{dsid}/accessions", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["accessions"] == ["ACC100", "ACC200"]


def test_export_csv_streams_with_header_and_rows(client, auth_headers, fake_trino):
    fake_trino(_SAMPLE_COLS, _sample_rows(2))
    fake_trino(["n"], [{"n": 2}])
    dsid = client.post(
        "/api/searches",
        json={"sql": _SQL},
        headers=auth_headers,
    ).json()["id"]

    fake_trino(
        ["primary_report_identifier", "accession_number"],
        [
            {"primary_report_identifier": "s3://bucket/1", "accession_number": "ACC1"},
            {
                "primary_report_identifier": "s3://bucket/2,and,commas",
                "accession_number": "ACC2",
            },
        ],
    )
    r = client.get(f"/api/searches/{dsid}/csv", headers=auth_headers)
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert f"{dsid}.csv" in r.headers["content-disposition"]
    body = r.text
    lines = body.strip().splitlines()
    assert lines[0] == "primary_report_identifier,accession_number"
    assert lines[1] == "s3://bucket/1,ACC1"
    # Value with commas must be quoted.
    assert lines[2] == '"s3://bucket/2,and,commas",ACC2'


def test_export_csv_empty_search_returns_header_only(client, auth_headers, fake_trino):
    fake_trino(_SAMPLE_COLS, _sample_rows(1))
    fake_trino(["n"], [{"n": 0}])
    dsid = client.post(
        "/api/searches",
        json={"sql": _SQL},
        headers=auth_headers,
    ).json()["id"]
    fake_trino(_SAMPLE_COLS, [])
    r = client.get(f"/api/searches/{dsid}/csv", headers=auth_headers)
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    assert lines == ["primary_report_identifier,accession_number"]
