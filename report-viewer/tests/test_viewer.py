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


def _make_search(client, auth_headers, fake_trino) -> str:
    fake_trino(_SAMPLE_COLS, _sample_rows(2))
    fake_trino(["n"], [{"n": 2}])
    return client.post(
        "/api/searches",
        json={"sql": _SQL},
        headers=auth_headers,
    ).json()["id"]


def test_export_csv_restricts_to_selected_columns_plus_id(
    client, auth_headers, fake_trino
):
    dsid = _make_search(client, auth_headers, fake_trino)
    fake_trino(_SAMPLE_COLS, _sample_rows(1))
    r = client.get(
        f"/api/searches/{dsid}/csv?columns=epic_mrn,accession_number",
        headers=auth_headers,
    )
    assert r.status_code == 200
    export_sql = fake_trino.calls[-1][0]
    assert 's."epic_mrn"' in export_sql
    assert 's."accession_number"' in export_sql
    # ID is appended even though the client omitted it.
    assert 's."primary_report_identifier"' in export_sql
    assert "s.*" not in export_sql


def test_export_csv_applies_filters(client, auth_headers, fake_trino):
    dsid = _make_search(client, auth_headers, fake_trino)
    fake_trino(_SAMPLE_COLS, _sample_rows(1))
    r = client.get(
        f"/api/searches/{dsid}/csv?filter.modality=CT",
        headers=auth_headers,
    )
    assert r.status_code == 200
    export_sql, params = fake_trino.calls[-1]
    assert 's."modality" IN (?)' in export_sql
    assert params == ["CT"]


def test_export_csv_applies_sort(client, auth_headers, fake_trino):
    dsid = _make_search(client, auth_headers, fake_trino)
    fake_trino(_SAMPLE_COLS, _sample_rows(1))
    r = client.get(
        f"/api/searches/{dsid}/csv?sort=message_dt:desc",
        headers=auth_headers,
    )
    assert r.status_code == 200
    export_sql = fake_trino.calls[-1][0]
    assert 'ORDER BY s."message_dt" DESC NULLS LAST' in export_sql


def test_export_csv_does_not_duplicate_id_column(client, auth_headers, fake_trino):
    dsid = _make_search(client, auth_headers, fake_trino)
    fake_trino(_SAMPLE_COLS, _sample_rows(1))
    r = client.get(
        f"/api/searches/{dsid}/csv?columns=primary_report_identifier,accession_number",
        headers=auth_headers,
    )
    assert r.status_code == 200
    export_sql = fake_trino.calls[-1][0]
    assert export_sql.count('s."primary_report_identifier"') == 1


def test_export_csv_unsafe_column_is_400(client, auth_headers, fake_trino):
    dsid = _make_search(client, auth_headers, fake_trino)
    r = client.get(
        f"/api/searches/{dsid}/csv?columns=bad;col",
        headers=auth_headers,
    )
    assert r.status_code == 400
