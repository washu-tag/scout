"""Unit tests for the SDK's top-level query/connect/current_user surface.

Covers both identity modes: Voila (voila_svc JWT mint + caching, X-Trino-User
impersonation) and Jupyter (Hub-API access-token pass-through with no
impersonation), plus the anonymous fail-safe.
"""

import pytest

import scout
from scout import _identity, _query

from conftest import (
    requests_stub,
    set_hub_auth_state,
    set_token_response,
    trino_dbapi_stub,
)


def _connect_kwargs():
    """kwargs passed to the (stubbed) trino.dbapi.connect on the last call."""
    return trino_dbapi_stub.connect.call_args.kwargs


def _use_jupyter_env(monkeypatch):
    """Flip the autouse Voila env over to the Jupyter provider: drop the
    voila_svc client id and supply the Hub API vars the kernel would have."""
    monkeypatch.delenv("KEYCLOAK_VOILA_SVC_CLIENT_ID", raising=False)
    monkeypatch.setenv("JUPYTERHUB_API_TOKEN", "hub-tok")
    monkeypatch.setenv("JUPYTERHUB_API_URL", "http://hub:8081/hub/api")
    monkeypatch.setenv("JUPYTERHUB_USER", "alice")


def test_connect_impersonates_forwarded_user(monkeypatch):
    monkeypatch.setenv("X_AUTH_REQUEST_PREFERRED_USERNAME", "alice")
    set_token_response()

    scout.connect()

    kwargs = _connect_kwargs()
    assert kwargs["user"] == "alice"
    # SDK uses a dynamic JWT auth that re-fetches per request; assert on type
    # rather than equality with a static-token JWTAuthentication.
    assert isinstance(kwargs["auth"], _query._DynamicJWTAuthentication)
    assert kwargs["http_scheme"] == "https"
    assert kwargs["host"] == "trino.scout-analytics"
    assert kwargs["port"] == 8443
    assert kwargs["verify"] == "/etc/trino-ca/ca.crt"


def test_connect_falls_back_to_anonymous_when_no_user(monkeypatch):
    # No X_AUTH_REQUEST_PREFERRED_USERNAME in env -> anonymous (OPA clamps rows).
    set_token_response()

    scout.connect()

    assert _connect_kwargs()["user"] == "anonymous"


def test_token_is_minted_with_client_credentials(monkeypatch):
    monkeypatch.setenv("X_AUTH_REQUEST_PREFERRED_USERNAME", "bob")
    set_token_response()

    # connect() resolves the provider but doesn't mint until the auth's
    # callable is invoked. Call it directly to drive the mint.
    scout.connect()
    provider = _identity._resolve_provider()
    provider()

    assert requests_stub.post.call_count == 1
    url = requests_stub.post.call_args.args[0]
    data = requests_stub.post.call_args.kwargs["data"]
    assert url == "https://keycloak.test/token"
    assert data["grant_type"] == "client_credentials"
    assert data["client_id"] == "voila_svc"
    assert data["client_secret"] == "svc-secret"


def test_token_is_cached_across_calls(monkeypatch):
    monkeypatch.setenv("X_AUTH_REQUEST_PREFERRED_USERNAME", "alice")
    set_token_response(expires_in=3600)

    provider = _identity._resolve_provider()
    provider()
    provider()
    provider()

    # One mint, reused until ~expiry-60s elapses.
    assert requests_stub.post.call_count == 1


def test_token_refreshes_after_expiry(monkeypatch):
    monkeypatch.setenv("X_AUTH_REQUEST_PREFERRED_USERNAME", "alice")
    # expires_in below the refresh-before window -> immediately considered stale.
    set_token_response(expires_in=1)

    provider = _identity._resolve_provider()
    provider()
    provider()

    assert requests_stub.post.call_count == 2


def test_auth_session_pins_ca_against_env_bundle_override():
    # set_http_session must disable trust_env: requests substitutes
    # REQUESTS_CA_BUNDLE/CURL_CA_BUNDLE for the request-level verify and that
    # overrides session.verify (where the trino client puts TRINO_CA_CERT) —
    # package-proxy deployments set those vars to the staging bundle, which
    # lacks Trino's CA.
    import types

    auth = _query._DynamicJWTAuthentication(lambda: ("tok", None))
    session = types.SimpleNamespace(trust_env=True, auth=None)

    auth.set_http_session(session)

    assert session.trust_env is False
    assert isinstance(session.auth, _query._DynamicBearerAuth)


def test_jupyter_passthrough_omits_impersonation_user(monkeypatch):
    _use_jupyter_env(monkeypatch)
    set_hub_auth_state()

    scout.connect()

    kwargs = _connect_kwargs()
    # No X-Trino-User on the Jupyter path: connect() omits `user` so the trino
    # client sends no impersonation header and Trino reads identity from the
    # JWT principal. A stray user here would become an impersonation attempt.
    assert "user" not in kwargs
    assert isinstance(kwargs["auth"], _query._DynamicJWTAuthentication)


def test_jupyter_bearer_is_user_token_fetched_from_hub(monkeypatch):
    _use_jupyter_env(monkeypatch)
    token = set_hub_auth_state()

    bearer, user = _identity._resolve_provider()()

    assert bearer == token
    assert user is None  # pass-through, not impersonation
    # Fetches the owning user's record with the kernel's own Hub API token.
    call = requests_stub.get.call_args
    assert call.args[0] == "http://hub:8081/hub/api/users/alice"
    assert call.kwargs["headers"]["Authorization"] == "token hub-tok"


def test_jupyter_raises_when_auth_state_has_no_token(monkeypatch):
    _use_jupyter_env(monkeypatch)
    set_hub_auth_state(include_token=False)

    with pytest.raises(RuntimeError, match="auth_state has no access_token"):
        _identity._resolve_provider()()


def test_current_user_returns_voila_username(monkeypatch):
    monkeypatch.setenv("X_AUTH_REQUEST_PREFERRED_USERNAME", "dave")
    monkeypatch.setenv("JUPYTERHUB_USER", "hub-user")
    assert scout.current_user() == "dave"


def test_current_user_falls_back_to_hub_user(monkeypatch):
    monkeypatch.setenv("JUPYTERHUB_USER", "hub-user")
    assert scout.current_user() == "hub-user"


def test_current_user_none_when_no_identity(monkeypatch):
    # The public current_user() returns None for "anonymous" so callers can
    # distinguish "no identity" from a real user named anonymous.
    assert scout.current_user() is None
