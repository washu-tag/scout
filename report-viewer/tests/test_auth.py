from fastapi.testclient import TestClient

from scout_report_viewer.app import create_app


def test_unauthenticated_create_returns_401():
    with TestClient(create_app()) as client:
        r = client.post("/api/searches", json={"sql": "SELECT 1"})
        assert r.status_code == 401


def test_oauth2_proxy_header_authenticates():
    with TestClient(create_app()) as client:
        # Any non-401 response proves the username header authenticated us.
        r = client.post(
            "/api/searches",
            json={"sql": "SELECT 1"},
            headers={"X-Auth-Request-Preferred-Username": "alice"},
        )
        assert r.status_code != 401
