"""Tests for POST /api/reports/read table selection (#546/#536/#532).

These endpoints don't touch Postgres, so they use a bare TestClient +
fake_trino and run without REPORT_VIEWER_TEST_DATABASE_URL.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from scout_report_viewer.app import create_app
from scout_report_viewer.models import MAX_READ_REPORTS_IDS


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
    assert '"primary_report_identifier" IN (?)' in sql
    assert "contains" not in sql
    assert params == ["s3://b/1"]


def test_read_multiple_ids_one_placeholder_each(auth_headers, fake_trino):
    fake_trino(["accession_number"], [{"accession_number": "A1"}])
    with TestClient(create_app()) as client:
        r = _read(
            client,
            auth_headers,
            {"ids": ["A1", "A2", "A3"], "id_column": "accession_number"},
        )
    assert r.status_code == 200, r.text
    sql, params = fake_trino.calls[-1]
    assert '"accession_number" IN (?, ?, ?)' in sql
    assert params == ["A1", "A2", "A3"]


def test_read_empty_ids_skips_trino(auth_headers, fake_trino):
    with TestClient(create_app()) as client:
        r = _read(client, auth_headers, {"ids": []})
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == []
    assert fake_trino.calls == []


def test_read_too_many_ids_422(auth_headers, fake_trino):
    ids = [f"A{i}" for i in range(MAX_READ_REPORTS_IDS + 1)]
    with TestClient(create_app()) as client:
        r = _read(client, auth_headers, {"ids": ids})
    assert r.status_code == 422
    assert fake_trino.calls == []


def _with_highlights(monkeypatch) -> None:
    from scout_report_viewer.routes import reports

    async def expression(*_args):
        return "CAST(ARRAY[] AS JSON)"

    monkeypatch.setattr(reports, "_highlight_expression", expression)


def test_read_falls_back_without_highlights(auth_headers, fake_trino, monkeypatch):
    _with_highlights(monkeypatch)
    fake_trino.error()
    fake_trino(
        ["primary_report_identifier"], [{"primary_report_identifier": "s3://b/1"}]
    )
    with TestClient(create_app()) as client:
        r = _read(client, auth_headers, {"ids": ["s3://b/1"], "search_id": "s_1"})
    assert r.status_code == 200, r.text
    sent = [sql for sql, _ in fake_trino.calls]
    assert len(sent) == 2
    assert "ev_hits" in sent[0]
    assert "ev_hits" not in sent[1]


def test_read_does_not_retry_a_permission_denial(auth_headers, fake_trino, monkeypatch):
    _with_highlights(monkeypatch)
    fake_trino.error("PERMISSION_DENIED")
    with TestClient(create_app()) as client:
        r = _read(client, auth_headers, {"ids": ["s3://b/1"], "search_id": "s_1"})
    assert r.status_code == 400, r.text
    assert len(fake_trino.calls) == 1


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


def test_hits_parse_from_either_json_encoding() -> None:
    """A named ROW casts to a JSON object on some Trino versions and a
    positional array on others; neither should lose the marks."""
    from scout_report_viewer.routes.reports import _one_hit

    obj = _one_hit({"field": "report_text", "pos": 4, "len": 6, "polarity": "negative"})
    arr = _one_hit(["report_text", 4, 6, "negative"])
    assert obj == arr
    assert obj is not None and (obj.start, obj.end) == (3, 9)
    assert _one_hit({"field": "x", "pos": 0, "len": 6}) is None
    assert _one_hit("junk") is None
