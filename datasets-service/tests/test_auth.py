from fastapi.testclient import TestClient

from scout_datasets.app import create_app


def test_unauthenticated_create_returns_401():
    with TestClient(create_app()) as client:
        r = client.post("/datasets", json={"sql": "SELECT 1"})
        assert r.status_code == 401


def test_oauth2_proxy_header_authenticates():
    with TestClient(create_app()) as client:
        # No fake_trino enqueue → the route should reach Trino and 400
        # the empty-result path, NOT 401. Either outcome other than 401
        # proves the username header authenticated us.
        r = client.post(
            "/datasets",
            json={"sql": "SELECT 1"},
            headers={"X-Auth-Request-Preferred-Username": "alice"},
        )
        assert r.status_code != 401
