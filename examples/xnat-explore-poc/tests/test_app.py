from __future__ import annotations

import os

os.environ.setdefault("XNAT_EXPLORE_POC_INVOKE_TOKEN", "test-token")
os.environ.setdefault("XNAT_EXPLORE_POC_LANDING_BASE_URL", "https://xnat-demo.test")

from fastapi.testclient import TestClient

from xnat_explore_poc.app import invoke_app, landing_app

client = TestClient(invoke_app)
landing_client = TestClient(landing_app)

_ACTION_HEADERS = {"X-Report-Viewer-Action-Token": "test-token"}


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200


def test_landing_healthz():
    r = landing_client.get("/healthz")
    assert r.status_code == 200


def test_invoke_not_reachable_on_landing_app():
    """/invoke must be structurally absent from the public listener, not
    just NetworkPolicy-restricted - the two are separate FastAPI apps."""
    r = landing_client.post("/invoke", json={"search_id": "s_x"})
    assert r.status_code == 404


def test_landing_page_renders_reports_and_user():
    r = landing_client.get("/", params={"reports": "7", "user": "carol"})
    assert r.status_code == 200
    assert "Cohort of 7 reports received for carol." in r.text


def test_landing_page_escapes_user():
    r = landing_client.get(
        "/", params={"reports": "1", "user": "<script>alert(1)</script>"}
    )
    assert r.status_code == 200
    assert "<script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_invoke_requires_token():
    r = client.post("/invoke", json={"search_id": "s_x"})
    assert r.status_code == 401


def test_invoke_rejects_wrong_token(caplog):
    with caplog.at_level("WARNING"):
        r = client.post(
            "/invoke",
            json={"search_id": "s_x"},
            headers={"X-Report-Viewer-Action-Token": "wrong"},
        )
    assert r.status_code == 401
    assert "bad or missing action token" in caplog.text


def test_invoke_returns_url_with_timestamp():
    r = client.post(
        "/invoke",
        json={"search_id": "s_x", "username": "carol"},
        headers=_ACTION_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["url"].startswith("https://xnat-demo.test/?reports=0&user=carol&t=")
    ts = body["url"].rsplit("t=", 1)[1]
    assert ts.isdigit()


def test_invoke_logs_cohort_count_not_identifiers(caplog):
    """The cohort's identifiers are PHI-adjacent (a lake path, an
    accession number) - the log line should confirm a cohort of the
    right size arrived, not make its contents inspectable via Loki."""
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
            headers=_ACTION_HEADERS,
        )
    assert r.status_code == 200
    [record] = [rec for rec in caplog.records if "invoke:" in rec.message]
    assert "reports=2" in record.message
    assert "sub=carol" in record.message
    assert "s3://x/1" not in record.message
    assert "ACC1" not in record.message


def test_invoke_scrubs_newlines_from_logged_search_id(caplog):
    """This service's logs are plain text, not report-viewer's own JSON
    format - CR/LF must be stripped before a request-controlled value
    lands in a log message, or an attacker-controlled value could forge a
    fake subsequent log line."""
    malicious_search_id = "s_x\nWARNING xnat_explore_poc.app: forged line"
    with caplog.at_level("INFO"):
        r = client.post(
            "/invoke",
            json={"search_id": malicious_search_id},
            headers=_ACTION_HEADERS,
        )
    assert r.status_code == 200
    [record] = [rec for rec in caplog.records if "invoke:" in rec.message]
    assert "\n" not in record.message
    assert "forged line" in record.message
