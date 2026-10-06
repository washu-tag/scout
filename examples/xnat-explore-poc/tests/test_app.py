from __future__ import annotations

import os
import time

os.environ.setdefault("XNAT_EXPLORE_POC_INVOKE_TOKEN", "test-token")
os.environ.setdefault("XNAT_EXPLORE_POC_ASSERTION_KEY", "test-assertion-key")
os.environ.setdefault("XNAT_EXPLORE_POC_LANDING_BASE_URL", "https://xnat-demo.test")

from fastapi.testclient import TestClient
from jose import jwt

from xnat_explore_poc.app import invoke_app, landing_app
from xnat_explore_poc.config import settings

client = TestClient(invoke_app)
landing_client = TestClient(landing_app)

_ACTION_HEADERS = {"X-Report-Viewer-Action-Token": "test-token"}


def _assertion(
    search_id: str, sub: str = "carol", roles=None, exp_delta: int = 60
) -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        "roles": roles or [],
        "search_id": search_id,
        "action_id": "explore-xnat",
        "iat": now,
        "exp": now + exp_delta,
    }
    return jwt.encode(claims, "test-assertion-key", algorithm="HS256")


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
    """The page has its own legitimate <script> block (the sandbox-inheritance
    probes, #739) - this checks the user-supplied payload specifically isn't
    injected unescaped, not that no <script> tag exists anywhere on the page."""
    r = landing_client.get(
        "/", params={"reports": "1", "user": "<script>alert(1)</script>"}
    )
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text


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


def test_invoke_requires_user_assertion(caplog):
    """The bearer action token alone only proves the caller knows a
    shared secret - it doesn't say who the invocation is for. Missing
    assertion must be rejected, not silently allowed through."""
    with caplog.at_level("WARNING"):
        r = client.post("/invoke", json={"search_id": "s_x"}, headers=_ACTION_HEADERS)
    assert r.status_code == 401
    assert "missing user assertion" in caplog.text


def test_invoke_rejects_assertion_signed_with_wrong_key(caplog):
    """A forged assertion (e.g. signed with the leaked invoke_token
    instead of the real assertion key) must not verify."""
    now = int(time.time())
    forged = jwt.encode(
        {
            "sub": "attacker",
            "roles": ["report-viewer-admin"],
            "search_id": "s_x",
            "action_id": "explore-xnat",
            "iat": now,
            "exp": now + 60,
        },
        "test-token",  # the invoke token, not the assertion key
        algorithm="HS256",
    )
    with caplog.at_level("WARNING"):
        r = client.post(
            "/invoke",
            json={"search_id": "s_x"},
            headers={**_ACTION_HEADERS, "X-Report-Viewer-User-Assertion": forged},
        )
    assert r.status_code == 401
    assert "invalid user assertion signature" in caplog.text


def test_invoke_rejects_expired_assertion(caplog):
    with caplog.at_level("WARNING"):
        r = client.post(
            "/invoke",
            json={"search_id": "s_x"},
            headers={
                **_ACTION_HEADERS,
                "X-Report-Viewer-User-Assertion": _assertion("s_x", exp_delta=-1),
            },
        )
    assert r.status_code == 401
    assert "user assertion expired" in caplog.text


def test_invoke_rejects_search_id_mismatch(caplog):
    """Catches a naive replay: reusing a captured assertion against a
    different search's body without updating search_id to match. Not a
    guarantee against a deliberate forgery - see app.py's comment on this
    check for what it doesn't cover."""
    with caplog.at_level("WARNING"):
        r = client.post(
            "/invoke",
            json={"search_id": "s_other"},
            headers={
                **_ACTION_HEADERS,
                "X-Report-Viewer-User-Assertion": _assertion("s_x"),
            },
        )
    assert r.status_code == 401
    assert "assertion search_id=s_x != request search_id=s_other" in caplog.text


def test_invoke_enforces_required_role(monkeypatch, caplog):
    monkeypatch.setattr(settings, "required_role", "report-viewer-admin")
    with caplog.at_level("WARNING"):
        r = client.post(
            "/invoke",
            json={"search_id": "s_x"},
            headers={
                **_ACTION_HEADERS,
                "X-Report-Viewer-User-Assertion": _assertion(
                    "s_x", roles=["report-viewer-user"]
                ),
            },
        )
    assert r.status_code == 403
    assert "lacks required role report-viewer-admin" in caplog.text


def test_invoke_allows_caller_with_required_role(monkeypatch):
    monkeypatch.setattr(settings, "required_role", "report-viewer-admin")
    r = client.post(
        "/invoke",
        json={"search_id": "s_x"},
        headers={
            **_ACTION_HEADERS,
            "X-Report-Viewer-User-Assertion": _assertion(
                "s_x", roles=["report-viewer-admin"]
            ),
        },
    )
    assert r.status_code == 200


def test_invoke_returns_url_with_timestamp():
    r = client.post(
        "/invoke",
        json={"search_id": "s_x"},
        headers={
            **_ACTION_HEADERS,
            "X-Report-Viewer-User-Assertion": _assertion("s_x"),
        },
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
            headers={
                **_ACTION_HEADERS,
                "X-Report-Viewer-User-Assertion": _assertion("s_x", sub="carol"),
            },
        )
    assert r.status_code == 200
    [record] = [rec for rec in caplog.records if "invoke:" in rec.message]
    assert "reports=2" in record.message
    assert "sub=carol" in record.message
    assert "s3://x/1" not in record.message
    assert "ACC1" not in record.message


def test_invoke_scrubs_newlines_from_logged_search_id(caplog):
    """search_id is logged before the user assertion is even checked, so
    it's reachable with just a leaked invokeToken (no valid assertion
    needed to reach this line). This service's logs are plain text, not
    report-viewer's own JSON format - CR/LF must be stripped here or an
    attacker-controlled value could forge a fake subsequent log line."""
    malicious_search_id = "s_x\nWARNING xnat_explore_poc.app: forged line"
    with caplog.at_level("WARNING"):
        r = client.post(
            "/invoke",
            json={"search_id": malicious_search_id},
            headers=_ACTION_HEADERS,
        )
    assert r.status_code == 401
    [record] = [
        rec for rec in caplog.records if "missing user assertion" in rec.message
    ]
    assert "\n" not in record.message
    assert "forged line" in record.message
