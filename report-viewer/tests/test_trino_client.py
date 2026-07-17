"""Tests for the svc-principal token cache backing Trino calls."""

from __future__ import annotations

import base64
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from scout_report_viewer import trino_client
from scout_report_viewer.config import settings


def _make_jwt(exp_in_seconds: int = 14400) -> str:
    now = int(time.time())
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = {
        "iat": now,
        "exp": now + exp_in_seconds,
        "aud": "trino",
        "azp": "report_viewer_svc",
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}."


@pytest.fixture
def configured_svc(monkeypatch):
    monkeypatch.setattr(
        settings, "trino_auth_token_url", "https://kc.example/token", raising=False
    )
    monkeypatch.setattr(
        settings, "trino_auth_client_id", "report_viewer_svc", raising=False
    )
    monkeypatch.setattr(settings, "trino_auth_client_secret", "shh", raising=False)


def test_cache_fetches_on_first_get(configured_svc):
    cache = trino_client._SvcTokenCache()
    jwt = _make_jwt()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"access_token": jwt, "expires_in": 14400}
    mock_resp.raise_for_status.return_value = None
    with patch(
        "scout_report_viewer.trino_client.httpx.post", return_value=mock_resp
    ) as mock_post:
        token = cache.get()
        assert token == jwt
        assert mock_post.call_count == 1
        kwargs = mock_post.call_args.kwargs
        assert kwargs["data"]["grant_type"] == "client_credentials"
        assert kwargs["data"]["client_id"] == "report_viewer_svc"


def test_cache_reuses_within_lifetime(configured_svc):
    cache = trino_client._SvcTokenCache()
    jwt = _make_jwt(14400)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"access_token": jwt}
    mock_resp.raise_for_status.return_value = None
    with patch(
        "scout_report_viewer.trino_client.httpx.post", return_value=mock_resp
    ) as mock_post:
        cache.get()
        cache.get()
        cache.get()
        assert mock_post.call_count == 1


def test_cache_refetches_after_invalidate(configured_svc):
    cache = trino_client._SvcTokenCache()
    jwt = _make_jwt()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"access_token": jwt}
    mock_resp.raise_for_status.return_value = None
    with patch(
        "scout_report_viewer.trino_client.httpx.post", return_value=mock_resp
    ) as mock_post:
        cache.get()
        cache.invalidate()
        cache.get()
        assert mock_post.call_count == 2


def test_cache_raises_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "trino_auth_token_url", "", raising=False)
    cache = trino_client._SvcTokenCache()
    with pytest.raises(RuntimeError, match="credentials not configured"):
        cache.get()


def test_token_lifetime_prefers_jwt_claims():
    assert trino_client._token_lifetime(_make_jwt(14400), expires_in=999) == 14400


def test_token_lifetime_falls_back_to_expires_in_when_jwt_unparseable():
    assert trino_client._token_lifetime("not.a.jwt", expires_in=600) == 600


def test_token_lifetime_floor_when_nothing_useful():
    assert (
        trino_client._token_lifetime("not.a.jwt", expires_in=None)
        == trino_client._FALLBACK_LIFETIME_SECONDS
    )
