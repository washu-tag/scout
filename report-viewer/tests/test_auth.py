import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from scout_report_viewer.app import create_app
from scout_report_viewer.auth import get_current_user
from scout_report_viewer.config import settings


def test_unauthenticated_create_returns_401():
    with TestClient(create_app()) as client:
        r = client.post("/api/searches", json={"sql": "SELECT 1"})
        assert r.status_code == 401


async def test_header_path_trusted_with_gateway_secret(monkeypatch):
    monkeypatch.setattr(settings, "gateway_secret", "s3cret")
    user = await get_current_user(
        authorization=None,
        x_auth_request_preferred_username="alice",
        x_report_viewer_gateway="s3cret",
    )
    assert user.sub == "alice"


@pytest.mark.parametrize("gateway", [None, "", "wrong", "\xff"])
async def test_header_path_rejected_without_matching_secret(monkeypatch, gateway):
    monkeypatch.setattr(settings, "gateway_secret", "s3cret")
    with pytest.raises(HTTPException) as exc:
        await get_current_user(
            authorization=None,
            x_auth_request_preferred_username="alice",
            x_report_viewer_gateway=gateway,
        )
    assert exc.value.status_code == 401


async def test_header_path_rejected_when_secret_unset(monkeypatch):
    monkeypatch.setattr(settings, "gateway_secret", "")
    with pytest.raises(HTTPException) as exc:
        await get_current_user(
            authorization=None,
            x_auth_request_preferred_username="alice",
            x_report_viewer_gateway="anything",
        )
    assert exc.value.status_code == 401
