"""End-to-end tests for the /api/searches routes.

Skipped unless `REPORT_VIEWER_TEST_DATABASE_URL` is exported - they exercise
the real Postgres schema with a mocked Trino. Run locally with:

    docker run --rm -d -p 55432:5432 -e POSTGRES_PASSWORD=test \\
        -e POSTGRES_DB=searches_test postgres:16
    REPORT_VIEWER_TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/searches_test \\
        pytest tests/test_searches.py -v
"""

from __future__ import annotations


_SQL_HAPPY = (
    "SELECT primary_report_identifier, accession_number, modality "
    "FROM reports_latest"
)


def _sample_columns() -> list[str]:
    return ["primary_report_identifier", "accession_number", "modality"]


def _sample_rows() -> list[dict]:
    return [
        {
            "primary_report_identifier": "s3://bucket/1",
            "accession_number": "ACC1",
            "modality": "CT",
        },
        {
            "primary_report_identifier": "s3://bucket/2",
            "accession_number": "ACC2",
            "modality": "MR",
        },
        {
            "primary_report_identifier": "s3://bucket/3",
            "accession_number": "ACC3",
            "modality": "CT",
        },
    ]


def test_create_search_happy_path(client, auth_headers, fake_trino):
    fake_trino(_sample_columns(), _sample_rows())
    fake_trino(["n"], [{"n": 3}])
    r = client.post(
        "/api/searches",
        json={"sql": _SQL_HAPPY},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["count"] == 3
    assert body["id_column"] == "primary_report_identifier"
    assert body["id"].startswith("s_")
    assert body["columns"] == _sample_columns()
    assert len(body["sample"]) == 3
    assert len(body["evidence"]) == 3
    for ev in body["evidence"]:
        assert ev["excerpt"] is None
        assert ev["matched_diagnoses"] == []
        assert "primary_report_identifier" in ev
    assert "summary" not in body
    assert body["view_url"].endswith(f"/spa/searches/{body['id']}")


def test_create_search_empty_result_is_201(client, auth_headers, fake_trino):
    fake_trino(_sample_columns(), [])
    fake_trino(["n"], [{"n": 0}])
    r = client.post(
        "/api/searches",
        json={"sql": _SQL_HAPPY + " WHERE 1=0"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["count"] == 0
    assert body["sample"] == []
    assert body["evidence"] == []
    assert body["columns"] == _sample_columns()


def test_create_search_missing_primary_report_identifier_is_400(
    client, auth_headers, fake_trino
):
    fake_trino(
        ["accession_number", "modality"],
        [{"accession_number": "ACC1", "modality": "CT"}],
    )
    r = client.post(
        "/api/searches",
        json={"sql": "SELECT accession_number, modality FROM reports_latest"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "primary_report_identifier" in r.text


def test_create_search_missing_accession_number_is_400(
    client, auth_headers, fake_trino
):
    fake_trino(
        ["primary_report_identifier", "modality"],
        [{"primary_report_identifier": "s3://bucket/1", "modality": "CT"}],
    )
    r = client.post(
        "/api/searches",
        json={"sql": "SELECT primary_report_identifier, modality FROM reports_latest"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "accession_number" in r.text


def test_get_meta_returns_404_for_other_user(client, auth_headers, fake_trino):
    fake_trino(_sample_columns(), _sample_rows())
    fake_trino(["n"], [{"n": 3}])
    r = client.post(
        "/api/searches",
        json={"sql": _SQL_HAPPY},
        headers=auth_headers,
    )
    dsid = r.json()["id"]
    r1 = client.get(f"/api/searches/{dsid}", headers=auth_headers)
    assert r1.status_code == 200

    r2 = client.get(
        f"/api/searches/{dsid}",
        headers={"X-Auth-Request-Preferred-Username": "bob"},
    )
    assert r2.status_code == 404


def test_delete_by_non_owner_is_404_and_leaves_row_intact(
    client, auth_headers, fake_trino
):
    fake_trino(_sample_columns(), _sample_rows())
    fake_trino(["n"], [{"n": 3}])
    dsid = client.post(
        "/api/searches",
        json={"sql": _SQL_HAPPY},
        headers=auth_headers,
    ).json()["id"]

    r = client.delete(
        f"/api/searches/{dsid}",
        headers={"X-Auth-Request-Preferred-Username": "bob"},
    )
    assert r.status_code == 404

    # Owner can still read it - the failed delete did not touch the row.
    r_owner = client.get(f"/api/searches/{dsid}", headers=auth_headers)
    assert r_owner.status_code == 200


def test_from_file_epic_mrn_routes_through_resolved_column_and_view(
    client, auth_headers, fake_trino
):
    fake_trino(["id"], [{"id": "EPIC1"}, {"id": "EPIC2"}])
    fake_trino(["n"], [{"n": 27}])
    csv = b"epic_mrn,extra\nEPIC1,x\nEPIC2,y\nEPIC1,z\n"
    r = client.post(
        "/api/searches/from-file",
        files={"file": ("cohort.csv", csv, "text/csv")},
        data={"id_column": "epic_mrn"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id_column"] == "epic_mrn"
    assert body["column_inferred"] is False
    assert body["count"] == 27
    assert body["unmatched"] == []
    assert body["unmatched_count"] == 0

    meta = client.get(f"/api/searches/{body['id']}", headers=auth_headers).json()
    assert "reports_latest_epic_view" in meta["sql"]
    assert '"resolved_epic_mrn" IN' in meta["sql"]
    assert '"epic_mrn" IN' not in meta["sql"]


def test_from_file_infers_column_from_header_alias(client, auth_headers, fake_trino):
    fake_trino(["id"], [{"id": "EPIC1"}])
    fake_trino(["n"], [{"n": 5}])
    csv = b"MRN,Study Date\nEPIC1,2024-01-01\n"
    r = client.post(
        "/api/searches/from-file",
        files={"file": ("cohort.csv", csv, "text/csv")},
        data={},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id_column"] == "epic_mrn"
    assert body["column_inferred"] is True


def test_from_file_reports_unmatched_ids(client, auth_headers, fake_trino):
    fake_trino(["id"], [{"id": "ACC1"}])
    fake_trino(["n"], [{"n": 3}])
    csv = b"accession_number\nACC1\nACC2\nACC3\n"
    r = client.post(
        "/api/searches/from-file",
        files={"file": ("cohort.csv", csv, "text/csv")},
        data={"id_column": "accession_number"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id_column"] == "accession_number"
    assert body["unmatched_count"] == 2
    assert set(body["unmatched"]) == {"ACC2", "ACC3"}


def test_from_file_multiple_candidates_prefers_accession(
    client, auth_headers, fake_trino
):
    fake_trino(["id"], [{"id": "ACC1"}])
    fake_trino(["n"], [{"n": 1}])
    csv = b"epic_mrn,accession_number\nEPIC1,ACC1\n"
    r = client.post(
        "/api/searches/from-file",
        files={"file": ("cohort.csv", csv, "text/csv")},
        data={},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id_column"] == "accession_number"
    assert body["column_inferred"] is True


def test_from_file_custom_sql_substitutes_cohort(client, auth_headers, fake_trino):
    fake_trino(["id"], [{"id": "EPIC1"}])
    # sample query runs before count when custom sql is passed
    fake_trino(
        ["primary_report_identifier", "accession_number"],
        [{"primary_report_identifier": "r1", "accession_number": "A1"}],
    )
    fake_trino(["n"], [{"n": 12}])
    csv = b"epic_mrn\nEPIC1\n"
    sql = (
        "SELECT primary_report_identifier, accession_number "
        "FROM reports_latest_epic_view "
        "WHERE {{cohort}} AND modality = 'CT'"
    )
    r = client.post(
        "/api/searches/from-file",
        files={"file": ("cohort.csv", csv, "text/csv")},
        data={"id_column": "epic_mrn", "sql": sql},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["count"] == 12

    meta = client.get(f"/api/searches/{body['id']}", headers=auth_headers).json()
    assert "{{cohort}}" not in meta["sql"]
    assert '"resolved_epic_mrn" IN' in meta["sql"]
    assert "modality = 'CT'" in meta["sql"]


def test_from_file_missing_cohort_placeholder_is_400(client, auth_headers, fake_trino):
    csv = b"epic_mrn\nEPIC1\n"
    r = client.post(
        "/api/searches/from-file",
        files={"file": ("cohort.csv", csv, "text/csv")},
        data={
            "id_column": "epic_mrn",
            "sql": "SELECT primary_report_identifier FROM reports_latest_epic_view",
        },
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "{{cohort}}" in r.text


def test_query_from_file_returns_rows_and_substitutes_cohort(
    client, auth_headers, fake_trino
):
    fake_trino(["id"], [{"id": "EPIC1"}])
    fake_trino(
        ["modality", "n"],
        [{"modality": "CT", "n": 5}, {"modality": "MR", "n": 3}],
    )
    csv = b"epic_mrn\nEPIC1\n"
    sql = (
        "SELECT modality, COUNT(*) AS n FROM reports_latest_epic_view "
        "WHERE {{cohort}} GROUP BY modality"
    )
    r = client.post(
        "/api/reports/query/from-file",
        files={"file": ("cohort.csv", csv, "text/csv")},
        data={"id_column": "epic_mrn", "sql": sql},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id_column"] == "epic_mrn"
    assert body["column_inferred"] is False
    assert body["rows"] == [
        {"modality": "CT", "n": 5},
        {"modality": "MR", "n": 3},
    ]
    assert body["unmatched"] == []
