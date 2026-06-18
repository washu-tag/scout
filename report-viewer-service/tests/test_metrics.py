"""Metrics surface tests — confirms /metrics renders and the custom
counters react to search creation. Postgres-backed."""

from __future__ import annotations


def test_metrics_endpoint_renders_prometheus_format():
    from fastapi.testclient import TestClient
    from scout_report_viewer.app import create_app

    with TestClient(create_app()) as client:
        r = client.get("/metrics")
        assert r.status_code == 200
        body = r.text
        # Standard HTTP histogram from prometheus-fastapi-instrumentator.
        assert "http_request_duration_seconds" in body
        # Our custom counters are defined at import; they appear with 0
        # samples until first increment, which is enough for this check.
        assert "scout_report_viewer_searches_created_total" in body
        assert "scout_report_viewer_trino_query_duration_seconds" in body


def test_create_search_increments_counters(client, auth_headers, fake_trino):
    # Pull the counter delta around a create — exact value isn't important
    # (other tests may bump it first), only that it moves by the count.
    from scout_report_viewer.metrics import SEARCHES_CREATED

    before_created = SEARCHES_CREATED.labels(
        kind="report", id_column="message_control_id", result="ok"
    )._value.get()

    fake_trino(
        ["message_control_id"],
        [
            {"message_control_id": "a"},
            {"message_control_id": "b"},
            {"message_control_id": "c"},
        ],
    )
    r = client.post(
        "/searches",
        json={"sql": "SELECT message_control_id FROM reports_latest"},
        headers=auth_headers,
    )
    assert r.status_code == 201

    after_created = SEARCHES_CREATED.labels(
        kind="report", id_column="message_control_id", result="ok"
    )._value.get()
    assert after_created == before_created + 1
