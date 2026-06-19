from fastapi.testclient import TestClient

from scout_report_viewer.app import create_app


def test_healthz_returns_ok():
    # Schema setup may fail in CI without Postgres; lifespan tolerates it.
    with TestClient(create_app()) as client:
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_root_returns_service_identity():
    with TestClient(create_app()) as client:
        r = client.get("/")
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == "report-viewer-service"
        assert "version" in body
