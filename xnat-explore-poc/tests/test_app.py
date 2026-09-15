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
        "/invoke", json={"search_id": "s_x"}, headers={"X-Report-Viewer-Action-Token": "wrong"}
    )
    assert r.status_code == 401


def test_invoke_returns_url_with_timestamp():
    r = client.post(
        "/invoke", json={"search_id": "s_x"}, headers={"X-Report-Viewer-Action-Token": "test-token"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["url"].startswith("https://xnat.test?t=")
    ts = body["url"].split("t=")[1]
    assert ts.isdigit()
