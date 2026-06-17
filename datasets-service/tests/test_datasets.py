"""End-to-end tests for the /datasets routes.

Skipped unless `DATASETS_TEST_DATABASE_URL` is exported — they exercise
the real Postgres schema with a mocked Trino. Run locally with:

    docker run --rm -d -p 55432:5432 -e POSTGRES_PASSWORD=test \\
        -e POSTGRES_DB=datasets_test postgres:16
    DATASETS_TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/datasets_test \\
        pytest tests/test_datasets.py -v
"""

from __future__ import annotations


def _sample_columns() -> list[str]:
    return [
        "message_control_id",
        "modality",
        "year",
        "service_name",
    ]


def _sample_rows() -> list[dict]:
    return [
        {
            "message_control_id": "m1",
            "modality": "CT",
            "year": 2024,
            "service_name": "CT BRAIN",
        },
        {
            "message_control_id": "m2",
            "modality": "MR",
            "year": 2024,
            "service_name": "MR BRAIN",
        },
        {
            "message_control_id": "m3",
            "modality": "CT",
            "year": 2023,
            "service_name": "CT CHEST",
        },
    ]


def test_create_dataset_happy_path(client, auth_headers, fake_trino):
    fake_trino(_sample_columns(), _sample_rows())
    r = client.post(
        "/datasets",
        json={
            "sql": "SELECT message_control_id, modality, year, service_name FROM reports_latest"
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["count"] == 3
    assert body["id_column"] == "message_control_id"
    assert body["dataset_id"].startswith("ds_")
    assert len(body["sample"]) == 3
    assert body["view_url"].endswith(f"/datasets/{body['dataset_id']}/view")


def test_create_dataset_requires_known_id_column(client, auth_headers, fake_trino):
    fake_trino(["modality", "year"], [{"modality": "CT", "year": 2024}])
    r = client.post(
        "/datasets",
        json={"sql": "SELECT modality, year FROM reports_latest"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "id_column" in r.text or "message_control_id" in r.text


def test_create_dataset_explicit_id_column(client, auth_headers, fake_trino):
    fake_trino(
        ["my_id", "modality"],
        [{"my_id": "a", "modality": "CT"}, {"my_id": "b", "modality": "MR"}],
    )
    r = client.post(
        "/datasets",
        json={
            "sql": "SELECT my_id, modality FROM reports_latest",
            "id_column": "my_id",
        },
        headers=auth_headers,
    )
    assert r.status_code == 201
    assert r.json()["id_column"] == "my_id"
    assert r.json()["count"] == 2


def test_get_meta_returns_404_for_other_user(client, auth_headers, fake_trino):
    fake_trino(_sample_columns(), _sample_rows())
    r = client.post(
        "/datasets",
        json={"sql": "SELECT message_control_id FROM reports_latest"},
        headers=auth_headers,
    )
    dsid = r.json()["dataset_id"]
    # alice can see hers.
    r1 = client.get(f"/datasets/{dsid}", headers=auth_headers)
    assert r1.status_code == 200

    # bob cannot.
    r2 = client.get(
        f"/datasets/{dsid}",
        headers={"X-Auth-Request-Preferred-Username": "bob"},
    )
    assert r2.status_code == 404


def test_get_rows_paginates_in_id_list_order(client, auth_headers, fake_trino):
    # Materialize with 5 IDs.
    fake_trino(
        ["message_control_id"],
        [{"message_control_id": f"m{i}"} for i in range(5)],
    )
    r = client.post(
        "/datasets",
        json={"sql": "SELECT message_control_id FROM reports_latest"},
        headers=auth_headers,
    )
    dsid = r.json()["dataset_id"]

    # Page 1 limit 2 — Trino returns the rows in reverse order; the route
    # is supposed to re-sort to match the materialized order.
    fake_trino(
        ["message_control_id", "modality"],
        [
            {"message_control_id": "m1", "modality": "CT"},
            {"message_control_id": "m0", "modality": "MR"},
        ],
    )
    r = client.get(f"/datasets/{dsid}/rows?page=1&limit=2", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["page"] == 1
    assert body["limit"] == 2
    assert body["total"] == 5
    assert [row["message_control_id"] for row in body["rows"]] == ["m0", "m1"]


def test_get_rows_returns_empty_past_end(client, auth_headers, fake_trino):
    fake_trino(["message_control_id"], [{"message_control_id": "only"}])
    dsid = client.post(
        "/datasets",
        json={"sql": "SELECT message_control_id FROM reports_latest"},
        headers=auth_headers,
    ).json()["dataset_id"]

    # No fake_trino enqueue — the route must short-circuit before calling Trino.
    r = client.get(f"/datasets/{dsid}/rows?page=99&limit=10", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["rows"] == []
