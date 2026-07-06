"""Metrics surface tests - confirms custom counters react to search creation."""

from __future__ import annotations


def test_create_search_increments_counters(client, auth_headers, fake_trino):
    # Pull the counter delta around a create - exact value isn't important
    # (other tests may bump it first), only that it moves by the count.
    from scout_report_viewer.metrics import SEARCHES_CREATED

    before_created = SEARCHES_CREATED._value.get()

    fake_trino(
        ["primary_report_identifier", "accession_number"],
        [
            {
                "primary_report_identifier": f"s3://bucket/{i}",
                "accession_number": f"ACC{i}",
            }
            for i in range(3)
        ],
    )
    fake_trino(["n"], [{"n": 3}])
    r = client.post(
        "/api/searches",
        json={
            "sql": "SELECT primary_report_identifier, accession_number FROM reports_latest"
        },
        headers=auth_headers,
    )
    assert r.status_code == 201

    after_created = SEARCHES_CREATED._value.get()
    assert after_created == before_created + 1
