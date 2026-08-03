"""Tests for POST /api/reports/read table selection (#546/#536/#532).

These endpoints don't touch Postgres, so they use a bare TestClient +
fake_trino and run without REPORT_VIEWER_TEST_DATABASE_URL.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from scout_report_viewer.app import create_app


def _read(client, auth_headers, body):
    return client.post("/api/reports/read", json=body, headers=auth_headers)


def test_read_defaults_to_curated_not_epic_view(auth_headers, fake_trino):
    # #546: the default must be a complete table (reports_curated), not the
    # epic view that hardcoded the bug.
    fake_trino(
        ["primary_report_identifier"], [{"primary_report_identifier": "s3://b/1"}]
    )
    with TestClient(create_app()) as client:
        r = _read(client, auth_headers, {"ids": ["s3://b/1"]})
    assert r.status_code == 200, r.text
    sql, params = fake_trino.calls[-1]
    assert "reports_curated" in sql
    assert "reports_latest_epic_view" not in sql
    assert params == [["s3://b/1"]]


def test_read_epic_mrn_default_matches_raw_column_on_curated(auth_headers, fake_trino):
    # epic_mrn with no table -> reports_curated, matching the RAW column.
    fake_trino(
        ["primary_report_identifier"], [{"primary_report_identifier": "s3://b/1"}]
    )
    with TestClient(create_app()) as client:
        r = _read(client, auth_headers, {"ids": ["123"], "id_column": "epic_mrn"})
    assert r.status_code == 200, r.text
    sql, _ = fake_trino.calls[-1]
    assert "reports_curated" in sql
    assert '"epic_mrn"' in sql
    assert "resolved_epic_mrn" not in sql


def test_read_explicit_table_honored(auth_headers, fake_trino):
    fake_trino(
        ["primary_report_identifier"], [{"primary_report_identifier": "s3://b/1"}]
    )
    with TestClient(create_app()) as client:
        r = _read(
            client,
            auth_headers,
            {"ids": ["s3://b/1"], "table": "reports_latest_epic_view"},
        )
    assert r.status_code == 200, r.text
    sql, _ = fake_trino.calls[-1]
    assert "reports_latest_epic_view" in sql


def test_read_unknown_table_400(auth_headers, fake_trino):
    with TestClient(create_app()) as client:
        r = _read(client, auth_headers, {"ids": ["x"], "table": "reports_secret"})
    assert r.status_code == 400
    assert "table must be one of" in r.text
    assert fake_trino.calls == []


def test_read_scout_patient_id_non_epic_400(auth_headers, fake_trino):
    # scout_patient_id exists only on epic views; default curated -> 400.
    with TestClient(create_app()) as client:
        r = _read(
            client, auth_headers, {"ids": ["p1"], "id_column": "scout_patient_id"}
        )
    assert r.status_code == 400
    assert "epic-view table" in r.text
    assert fake_trino.calls == []


def test_query_from_file_no_validation_binds_all_ids(auth_headers, fake_trino):
    # Custom SQL targets an unknown table, so there's no separate id-existence
    # validation: one query call, all submitted IDs bound.
    fake_trino(["n"], [{"n": 1}])
    with TestClient(create_app()) as client:
        r = client.post(
            "/api/reports/query/from-file",
            files={"file": ("cohort.csv", b"accession_number\nACC1\n", "text/csv")},
            data={
                "id_column": "accession_number",
                "sql": "SELECT COUNT(*) AS n FROM reports_latest WHERE {{cohort}}",
            },
            headers=auth_headers,
        )
    assert r.status_code == 200, r.text
    assert len(fake_trino.calls) == 1
    query_sql, params = fake_trino.calls[0]
    assert 'contains(?, "accession_number")' in query_sql
    assert params == [["ACC1"]]
