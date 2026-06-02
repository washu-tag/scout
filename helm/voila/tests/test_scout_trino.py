"""Unit tests for scout_trino — the Voila Trino client + impersonation helper.

Covers identity resolution (X-Trino-User from the forwarded username), the
voila_svc JWT mint + caching, the anonymous fail-safe, and the unauthenticated
trino-rw write path.
"""

import scout_trino
from conftest import requests_stub, trino_dbapi_stub, set_token_response


def _connect_kwargs():
    """kwargs passed to the (stubbed) trino.dbapi.connect on the last call."""
    return trino_dbapi_stub.connect.call_args.kwargs


def test_connect_impersonates_forwarded_user(monkeypatch):
    monkeypatch.setenv("X_AUTH_REQUEST_PREFERRED_USERNAME", "alice")
    set_token_response(access_token="tok-alice")

    scout_trino.connect()

    kwargs = _connect_kwargs()
    assert kwargs["user"] == "alice"
    assert kwargs["auth"] == scout_trino.JWTAuthentication("tok-alice")
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
    set_token_response(access_token="tok-bob")

    scout_trino.connect()

    assert requests_stub.post.call_count == 1
    url = requests_stub.post.call_args.args[0]
    data = requests_stub.post.call_args.kwargs["data"]
    assert url == "https://keycloak.test/token"
    assert data["grant_type"] == "client_credentials"
    assert data["client_id"] == "voila_svc"
    assert data["client_secret"] == "svc-secret"


def test_token_is_cached_across_connections(monkeypatch):
    monkeypatch.setenv("X_AUTH_REQUEST_PREFERRED_USERNAME", "alice")
    set_token_response(access_token="tok-1", expires_in=3600)

    scout_trino.connect()
    scout_trino.connect()
    scout_trino.connect()

    # One mint, reused by every connection until ~80% of its lifetime elapses.
    assert requests_stub.post.call_count == 1


def test_token_refreshes_after_expiry(monkeypatch):
    monkeypatch.setenv("X_AUTH_REQUEST_PREFERRED_USERNAME", "alice")
    # expires_in below the refresh-before window -> immediately considered stale.
    set_token_response(access_token="tok-1", expires_in=1)

    scout_trino.connect()
    scout_trino.connect()

    assert requests_stub.post.call_count == 2


def test_connect_writeback_targets_unauthenticated_rw_instance(monkeypatch):
    monkeypatch.setenv("X_AUTH_REQUEST_PREFERRED_USERNAME", "carol")

    scout_trino.connect_rw()

    kwargs = _connect_kwargs()
    assert kwargs["host"] == "trino-rw.scout-extractor"
    assert kwargs["port"] == 8080
    assert kwargs["http_scheme"] == "http"
    assert kwargs["user"] == "carol"
    # trino-rw is unauthenticated inside its NetworkPolicy boundary: no JWT mint.
    assert "auth" not in kwargs
    assert requests_stub.post.call_count == 0


def test_connect_writeback_audit_user_falls_back_to_anonymous(monkeypatch):
    # Fail-OPEN: a missing identity must not block a reviewer's annotation save.
    scout_trino.connect_rw()

    assert _connect_kwargs()["user"] == "anonymous"


def test_current_user_reads_env(monkeypatch):
    monkeypatch.setenv("X_AUTH_REQUEST_PREFERRED_USERNAME", "dave")
    assert scout_trino._current_user() == "dave"


def test_current_user_empty_without_env(monkeypatch):
    assert scout_trino._current_user() == ""
