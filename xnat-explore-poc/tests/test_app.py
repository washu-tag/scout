from __future__ import annotations

import os
import time

os.environ.setdefault("XNAT_EXPLORE_POC_INVOKE_TOKEN", "test-token")
os.environ.setdefault("XNAT_EXPLORE_POC_ASSERTION_KEY", "test-assertion-key")
os.environ.setdefault("XNAT_EXPLORE_POC_XNAT_BASE_URL", "https://xnat.test")

from fastapi.testclient import TestClient
from jose import jwt

from xnat_explore_poc.app import app
from xnat_explore_poc.config import settings

client = TestClient(app)

_ACTION_HEADERS = {"X-Report-Viewer-Action-Token": "test-token"}


def _assertion(search_id: str, sub: str = "carol", groups=None, exp_delta: int = 60) -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        "groups": groups or [],
        "search_id": search_id,
        "action_id": "explore-xnat",
        "iat": now,
        "exp": now + exp_delta,
    }
    return jwt.encode(claims, "test-assertion-key", algorithm="HS256")


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


def test_invoke_requires_user_assertion():
    """The bearer action token alone only proves the caller knows a
    shared secret - it doesn't say who the invocation is for. Missing
    assertion must be rejected, not silently allowed through."""
    r = client.post("/invoke", json={"search_id": "s_x"}, headers=_ACTION_HEADERS)
    assert r.status_code == 401


def test_invoke_rejects_assertion_signed_with_wrong_key():
    """A forged assertion (e.g. signed with the leaked invoke_token
    instead of the real assertion key) must not verify."""
    now = int(time.time())
    forged = jwt.encode(
        {
            "sub": "attacker",
            "groups": ["scout-admin"],
            "search_id": "s_x",
            "action_id": "explore-xnat",
            "iat": now,
            "exp": now + 60,
        },
        "test-token",  # the invoke token, not the assertion key
        algorithm="HS256",
    )
    r = client.post(
        "/invoke",
        json={"search_id": "s_x"},
        headers={**_ACTION_HEADERS, "X-Report-Viewer-User-Assertion": forged},
    )
    assert r.status_code == 401


def test_invoke_rejects_expired_assertion():
    r = client.post(
        "/invoke",
        json={"search_id": "s_x"},
        headers={
            **_ACTION_HEADERS,
            "X-Report-Viewer-User-Assertion": _assertion("s_x", exp_delta=-1),
        },
    )
    assert r.status_code == 401


def test_invoke_rejects_search_id_mismatch():
    """A captured assertion for one search can't be replayed against a
    different one within its validity window."""
    r = client.post(
        "/invoke",
        json={"search_id": "s_other"},
        headers={
            **_ACTION_HEADERS,
            "X-Report-Viewer-User-Assertion": _assertion("s_x"),
        },
    )
    assert r.status_code == 401


def test_invoke_enforces_required_group(monkeypatch):
    monkeypatch.setattr(settings, "required_group", "scout-admin")
    r = client.post(
        "/invoke",
        json={"search_id": "s_x"},
        headers={
            **_ACTION_HEADERS,
            "X-Report-Viewer-User-Assertion": _assertion("s_x", groups=["scout-user"]),
        },
    )
    assert r.status_code == 403


def test_invoke_allows_caller_with_required_group(monkeypatch):
    monkeypatch.setattr(settings, "required_group", "scout-admin")
    r = client.post(
        "/invoke",
        json={"search_id": "s_x"},
        headers={
            **_ACTION_HEADERS,
            "X-Report-Viewer-User-Assertion": _assertion("s_x", groups=["scout-admin"]),
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
    assert body["url"].startswith("https://xnat.test?t=")
    ts = body["url"].split("t=")[1]
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
