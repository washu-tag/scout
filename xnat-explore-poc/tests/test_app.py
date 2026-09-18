from __future__ import annotations

import os

os.environ.setdefault("XNAT_EXPLORE_POC_INVOKE_TOKEN", "test-token")
os.environ.setdefault("XNAT_EXPLORE_POC_XNAT_BASE_URL", "https://xnat.test")

from fastapi.testclient import TestClient

from xnat_explore_poc.app import app

client = TestClient(app)


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200


def test_invoke_requires_token():
    r = client.post("/invoke", json={"search_id": "s_x"})
    assert r.status_code == 401


def test_invoke_rejects_wrong_token():
    r = client.post(
        "/invoke",
        json={"search_id": "s_x"},
        headers={"X-Report-Viewer-Action-Token": "wrong"},
    )
    assert r.status_code == 401


def test_invoke_returns_url_with_timestamp():
    r = client.post(
        "/invoke",
        json={"search_id": "s_x"},
        headers={"X-Report-Viewer-Action-Token": "test-token"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["url"].startswith("https://xnat.test?t=")
    ts = body["url"].split("t=")[1]
    assert ts.isdigit()


def test_invoke_logs_the_received_cohort(caplog):
    reports = [
        {"primary_report_identifier": "s3://x/1", "accession_number": "ACC1"},
        {"primary_report_identifier": "s3://x/2", "accession_number": None},
    ]
    with caplog.at_level("INFO"):
        r = client.post(
            "/invoke",
            json={
                "search_id": "s_x",
                "username": "carol",
                "reports": reports,
                "cohort_truncated": False,
            },
            headers={"X-Report-Viewer-Action-Token": "test-token"},
        )
    assert r.status_code == 200
    [record] = [rec for rec in caplog.records if "invoke:" in rec.message]
    assert "reports=2" in record.message
    assert "s3://x/1" in record.message
    assert "ACC1" in record.message
