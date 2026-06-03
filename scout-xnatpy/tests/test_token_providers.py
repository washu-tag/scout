"""Tests for JupyterHubTokenProvider env-var validation and auth_state parsing."""

from __future__ import annotations

import pytest

from scout_xnat import token_providers
from scout_xnat.errors import ScoutXnatAuthError
from scout_xnat.token_providers import JupyterHubTokenProvider

_ENV_VARS = ("JUPYTERHUB_API_URL", "JUPYTERHUB_API_TOKEN", "JUPYTERHUB_USER")


class FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json


@pytest.fixture
def hub_env(monkeypatch):
    monkeypatch.setenv("JUPYTERHUB_API_URL", "http://hub.local/hub/api")
    monkeypatch.setenv("JUPYTERHUB_API_TOKEN", "api-token")
    monkeypatch.setenv("JUPYTERHUB_USER", "alice")


@pytest.mark.parametrize("missing", _ENV_VARS)
def test_missing_env_var_raises(monkeypatch, hub_env, missing):
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(ScoutXnatAuthError) as exc:
        JupyterHubTokenProvider()
    assert missing in str(exc.value)


def test_happy_path_returns_access_token(monkeypatch, hub_env):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return FakeResponse(200, {"auth_state": {"access_token": "kc-access-token"}})

    monkeypatch.setattr(token_providers.requests, "get", fake_get)

    provider = JupyterHubTokenProvider()
    token = provider()

    assert token == "kc-access-token"
    assert captured["url"] == "http://hub.local/hub/api/users/alice"
    assert captured["headers"] == {"Authorization": "token api-token"}


def test_non_200_raises(monkeypatch, hub_env):
    monkeypatch.setattr(
        token_providers.requests,
        "get",
        lambda *a, **k: FakeResponse(403, text="forbidden"),
    )
    provider = JupyterHubTokenProvider()
    with pytest.raises(ScoutXnatAuthError) as exc:
        provider()
    assert "403" in str(exc.value)


def test_401_raises_session_expired_message(monkeypatch, hub_env):
    # A 401 means the Hub couldn't refresh us (expired SSO session); the error
    # should tell the user to re-login, not dump a raw status line.
    monkeypatch.setattr(
        token_providers.requests,
        "get",
        lambda *a, **k: FakeResponse(401, text="Unauthorized"),
    )
    provider = JupyterHubTokenProvider()
    with pytest.raises(ScoutXnatAuthError) as exc:
        provider()
    assert "re-login" in str(exc.value).lower()


def test_missing_auth_state_raises(monkeypatch, hub_env):
    monkeypatch.setattr(
        token_providers.requests, "get", lambda *a, **k: FakeResponse(200, {})
    )
    provider = JupyterHubTokenProvider()
    with pytest.raises(ScoutXnatAuthError) as exc:
        provider()
    assert "auth_state" in str(exc.value)


def test_missing_access_token_raises(monkeypatch, hub_env):
    monkeypatch.setattr(
        token_providers.requests,
        "get",
        lambda *a, **k: FakeResponse(200, {"auth_state": {"other": "x"}}),
    )
    provider = JupyterHubTokenProvider()
    with pytest.raises(ScoutXnatAuthError) as exc:
        provider()
    assert "access_token" in str(exc.value)


def test_request_exception_raises(monkeypatch, hub_env):
    import requests

    def boom(*a, **k):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr(token_providers.requests, "get", boom)
    provider = JupyterHubTokenProvider()
    with pytest.raises(ScoutXnatAuthError) as exc:
        provider()
    assert "unreachable" in str(exc.value)
