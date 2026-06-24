"""Tests for the viewer page + accessions + CSV export."""

from __future__ import annotations


def test_view_returns_html_for_owner(client, auth_headers, fake_trino):
    fake_trino(["message_control_id"], [{"message_control_id": "m1"}])
    dsid = client.post(
        "/api/searches",
        json={"sql": "SELECT message_control_id FROM reports_latest"},
        headers=auth_headers,
    ).json()["search_id"]

    r = client.get(f"/api/searches/{dsid}/view", headers=auth_headers)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    # Page contains the search id and the Tabulator integrity tag —
    # not just an empty shell.
    assert dsid in r.text
    assert "tabulator.min.js" in r.text
    assert "integrity=" in r.text


def test_view_404_for_other_user(client, auth_headers, fake_trino):
    fake_trino(["message_control_id"], [{"message_control_id": "m1"}])
    dsid = client.post(
        "/api/searches",
        json={"sql": "SELECT message_control_id FROM reports_latest"},
        headers=auth_headers,
    ).json()["search_id"]

    r = client.get(
        f"/api/searches/{dsid}/view",
        headers={"X-Auth-Request-Preferred-Username": "bob"},
    )
    assert r.status_code == 404


def test_accessions_returns_deduped_list(client, auth_headers, fake_trino):
    fake_trino(
        ["message_control_id"], [{"message_control_id": f"m{i}"} for i in range(3)]
    )
    dsid = client.post(
        "/api/searches",
        json={"sql": "SELECT message_control_id FROM reports_latest"},
        headers=auth_headers,
    ).json()["search_id"]

    fake_trino(
        ["accession_number"],
        [{"accession_number": "ACC100"}, {"accession_number": "ACC200"}],
    )
    r = client.get(f"/api/searches/{dsid}/accessions", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["accessions"] == ["ACC100", "ACC200"]


def test_export_csv_streams_with_header_and_rows(client, auth_headers, fake_trino):
    fake_trino(
        ["message_control_id"],
        [{"message_control_id": "m1"}, {"message_control_id": "m2"}],
    )
    dsid = client.post(
        "/api/searches",
        json={"sql": "SELECT message_control_id FROM reports_latest"},
        headers=auth_headers,
    ).json()["search_id"]

    fake_trino(
        [
            "message_control_id",
            "accession_number",
            "modality",
            "service_name",
            "message_dt",
            "patient_age",
            "sex",
        ],
        [
            {
                "message_control_id": "m1",
                "accession_number": "ACC1",
                "modality": "CT",
                "service_name": "CT BRAIN",
                "message_dt": "2024-01-01",
                "patient_age": 42,
                "sex": "F",
            },
            {
                "message_control_id": "m2",
                "accession_number": "ACC2",
                "modality": "MR",
                "service_name": 'MR "BRAIN"',  # quote in value
                "message_dt": "2024-01-02",
                "patient_age": 30,
                "sex": "M",
            },
        ],
    )
    r = client.get(f"/api/searches/{dsid}/csv", headers=auth_headers)
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert f"{dsid}.csv" in r.headers["content-disposition"]
    body = r.text
    lines = body.strip().splitlines()
    assert (
        lines[0]
        == "message_control_id,accession_number,modality,service_name,message_dt,patient_age,sex"
    )
    assert lines[1].startswith("m1,ACC1,CT,CT BRAIN,")
    # Second row's service_name has an embedded quote — must be CSV-escaped.
    assert '"MR ""BRAIN"""' in lines[2]


def test_export_csv_empty_search_returns_header_only(client, auth_headers, fake_trino):
    # We can't actually have an empty search (POST rejects), but a
    # subsequent refine that strips it to 0 rows would. Simulate via
    # direct store insert in the future. For now: verify the generator
    # handles the empty id_list gracefully — only the header line.
    fake_trino(["message_control_id"], [{"message_control_id": "only"}])
    dsid = client.post(
        "/api/searches",
        json={"sql": "SELECT message_control_id FROM reports_latest"},
        headers=auth_headers,
    ).json()["search_id"]
    # Empty Trino response for the export step → 1 chunk → 0 rows.
    fake_trino(
        [
            "message_control_id",
            "accession_number",
            "modality",
            "service_name",
            "message_dt",
            "patient_age",
            "sex",
        ],
        [],
    )
    r = client.get(f"/api/searches/{dsid}/csv", headers=auth_headers)
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    assert lines == [
        "message_control_id,accession_number,modality,service_name,message_dt,patient_age,sex"
    ]
