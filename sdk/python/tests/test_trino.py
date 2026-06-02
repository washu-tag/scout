"""Unit tests for scout.trino - the SDK's Trino client + impersonation helper.

Covers identity resolution (X-Trino-User from the forwarded username), the
voila_svc JWT mint + caching, and the anonymous fail-safe. connect_rw lives
in scout.voila now (Voila-only); see test_voila.py for that side.
"""

from scout import _identity
from scout import trino as scout_trino

from conftest import requests_stub, set_token_response, trino_dbapi_stub


def _connect_kwargs():
    """kwargs passed to the (stubbed) trino.dbapi.connect on the last call."""
    return trino_dbapi_stub.connect.call_args.kwargs


def test_connect_impersonates_forwarded_user(monkeypatch):
    monkeypatch.setenv("X_AUTH_REQUEST_PREFERRED_USERNAME", "alice")
    set_token_response()

    scout_trino.connect()

    kwargs = _connect_kwargs()
    assert kwargs["user"] == "alice"
    # SDK uses a dynamic JWT auth that re-fetches per request; assert on type
    # rather than equality with a static-token JWTAuthentication.
    assert isinstance(kwargs["auth"], scout_trino._DynamicJWTAuthentication)
    assert kwargs["http_scheme"] == "https"
    assert kwargs["host"] == "trino.scout-analytics"
    assert kwargs["port"] == 8443
    assert kwargs["verify"] == "/etc/trino-ca/ca.crt"


def test_connect_falls_back_to_anonymous_when_no_user(monkeypatch):
    # No X_AUTH_REQUEST_PREFERRED_USERNAME in env -> anonymous (OPA clamps rows).
    set_token_response()

    scout_trino.connect()

    assert _connect_kwargs()["user"] == "anonymous"


def test_token_is_minted_with_client_credentials(monkeypatch):
    monkeypatch.setenv("X_AUTH_REQUEST_PREFERRED_USERNAME", "bob")
    set_token_response()

    # connect() resolves the provider but doesn't mint until the auth's
    # callable is invoked. Call it directly to drive the mint.
    scout_trino.connect()
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


def test_resolve_audit_user_prefers_voila_username(monkeypatch):
    monkeypatch.setenv("X_AUTH_REQUEST_PREFERRED_USERNAME", "dave")
    monkeypatch.setenv("JUPYTERHUB_USER", "hub-user")
    assert _identity.resolve_audit_user() == "dave"


def test_resolve_audit_user_falls_back_to_hub_user(monkeypatch):
    monkeypatch.setenv("JUPYTERHUB_USER", "hub-user")
    assert _identity.resolve_audit_user() == "hub-user"


def test_resolve_audit_user_anonymous_when_neither_present(monkeypatch):
    assert _identity.resolve_audit_user() == "anonymous"
