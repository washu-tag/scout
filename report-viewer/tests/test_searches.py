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


def test_get_rows_paginates_in_id_list_order(client, auth_headers, fake_trino):
    fake_trino(
        ["primary_report_identifier", "accession_number"],
        [
            {
                "primary_report_identifier": f"s3://bucket/{i}",
                "accession_number": f"ACC{i}",
            }
            for i in range(5)
        ],
    )
    fake_trino(["n"], [{"n": 5}])
    r = client.post(
        "/api/searches",
        json={
            "sql": "SELECT primary_report_identifier, accession_number FROM reports_latest"
        },
        headers=auth_headers,
    )
    dsid = r.json()["id"]

    # Trino returns rows in reverse order; the route must re-sort to materialized order.
    fake_trino(
        ["primary_report_identifier", "accession_number"],
        [
            {"primary_report_identifier": "s3://bucket/1", "accession_number": "ACC1"},
            {"primary_report_identifier": "s3://bucket/0", "accession_number": "ACC0"},
        ],
    )
    r = client.get(f"/api/searches/{dsid}/rows?page=1&limit=2", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["page"] == 1
    assert body["limit"] == 2
    assert body["total"] == 5
    assert [row["primary_report_identifier"] for row in body["rows"]] == [
        "s3://bucket/0",
        "s3://bucket/1",
    ]


def test_get_rows_returns_empty_past_end(client, auth_headers, fake_trino):
    fake_trino(
        ["primary_report_identifier", "accession_number"],
        [{"primary_report_identifier": "s3://bucket/only", "accession_number": "ACC0"}],
    )
    fake_trino(["n"], [{"n": 1}])
    dsid = client.post(
        "/api/searches",
        json={
            "sql": "SELECT primary_report_identifier, accession_number FROM reports_latest"
        },
        headers=auth_headers,
    ).json()["id"]

    r = client.get(f"/api/searches/{dsid}/rows?page=99&limit=10", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["rows"] == []
