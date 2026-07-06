from fastapi.testclient import TestClient

from scout_report_viewer.app import create_app


def test_unauthenticated_create_returns_401():
    with TestClient(create_app()) as client:
        r = client.post("/api/searches", json={"sql": "SELECT 1"})
        assert r.status_code == 401
