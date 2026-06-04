"""Test harness for the scout SDK (sdk/python/src/scout/).

The SDK's _query/_identity modules import third-party packages (requests,
trino, sqlalchemy) that aren't installed in the unit-test environment. Stub
them in sys.modules before the modules are imported so the tests need nothing
but pytest.

Run with: cd sdk/python && PYTHONPATH=src pytest tests/ -v
"""

import sys
import types
from unittest.mock import MagicMock

import pytest


def _stub(name):
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


# --- requests: scout._identity.requests.post mints the voila_svc token ---
requests_stub = _stub("requests")
requests_stub.post = MagicMock(name="requests.post")
requests_stub.get = MagicMock(name="requests.get")

# requests.auth.AuthBase is the base class scout._query._DynamicBearerAuth
# extends. Stub it as a plain object so subclasses still work.
requests_auth_stub = _stub("requests.auth")


class _FakeAuthBase:
    pass


requests_auth_stub.AuthBase = _FakeAuthBase
requests_stub.auth = requests_auth_stub


# --- trino.dbapi.connect + trino.auth.Authentication ---
class FakeAuthentication:
    pass


class FakeHttpError(Exception):
    """Stand-in for trino.exceptions.HttpError; real one is a bare Exception
    subclass whose str() is 'error <status>: <body>'."""


trino_stub = _stub("trino")
trino_dbapi_stub = _stub("trino.dbapi")
trino_dbapi_stub.connect = MagicMock(name="trino.dbapi.connect")
trino_dbapi_stub.Connection = type("Connection", (), {})
trino_auth_stub = _stub("trino.auth")
trino_auth_stub.Authentication = FakeAuthentication
trino_exceptions_stub = _stub("trino.exceptions")
trino_exceptions_stub.HttpError = FakeHttpError
trino_stub.dbapi = trino_dbapi_stub
trino_stub.auth = trino_auth_stub
trino_stub.exceptions = trino_exceptions_stub


# --- sqlalchemy: scout._query uses create_engine + text ---
sqlalchemy_stub = _stub("sqlalchemy")
sqlalchemy_stub.create_engine = MagicMock(name="sqlalchemy.create_engine")
sqlalchemy_stub.text = MagicMock(name="sqlalchemy.text", side_effect=lambda s: s)
sqlalchemy_engine_stub = _stub("sqlalchemy.engine")
sqlalchemy_engine_stub.Engine = type("Engine", (), {})
sqlalchemy_stub.engine = sqlalchemy_engine_stub


# --- pandas: scout._query.query() imports pandas lazily and calls read_sql ---
pandas_stub = _stub("pandas")
pandas_stub.read_sql = MagicMock(name="pandas.read_sql")


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    """Each test starts with a fresh provider singleton, fresh mocks, and
    the Keycloak/Trino env the SDK expects."""
    from scout import _identity, _query

    _identity._singleton = None
    _identity._override_provider = None
    _query._engine = None
    requests_stub.post.reset_mock(return_value=True, side_effect=True)
    requests_stub.get.reset_mock(return_value=True, side_effect=True)
    trino_dbapi_stub.connect.reset_mock(return_value=True, side_effect=True)
    sqlalchemy_stub.create_engine.reset_mock(return_value=True, side_effect=True)
    pandas_stub.read_sql.reset_mock(return_value=True, side_effect=True)

    monkeypatch.delenv("X_AUTH_REQUEST_PREFERRED_USERNAME", raising=False)
    monkeypatch.delenv("JUPYTERHUB_API_TOKEN", raising=False)
    monkeypatch.delenv("JUPYTERHUB_API_URL", raising=False)
    monkeypatch.delenv("JUPYTERHUB_USER", raising=False)
    monkeypatch.setenv("KEYCLOAK_TOKEN_URL", "https://keycloak.test/token")
    monkeypatch.setenv("KEYCLOAK_VOILA_SVC_CLIENT_ID", "voila_svc")
    monkeypatch.setenv("KEYCLOAK_VOILA_SVC_CLIENT_SECRET", "svc-secret")
    monkeypatch.setenv("TRINO_HOST", "trino.scout-analytics")
    monkeypatch.setenv("TRINO_PORT", "8443")
    monkeypatch.setenv("TRINO_SCHEME", "https")
    monkeypatch.setenv("TRINO_CATALOG", "delta")
    monkeypatch.setenv("TRINO_SCHEMA", "default")
    monkeypatch.setenv("TRINO_CA_CERT", "/etc/trino-ca/ca.crt")
    yield


def _jwt_with_exp(expires_in):
    """A syntactically-valid JWT (header.payload.signature) with an `exp`
    claim. scout._identity._is_near_expiry decodes this to time the cache
    refresh. The signature is fake - we never verify."""
    import base64
    import json
    import time

    payload = {"exp": int(time.time()) + expires_in}
    payload_b64 = (
        base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    )
    return f"header.{payload_b64}.signature"


def jwt_with_claims(claims):
    """A syntactically-valid JWT (header.payload.signature) whose payload is
    exactly `claims`. Unlike _jwt_with_exp, the caller controls the claim set,
    so tests can build tokens with an absolute `exp`, a missing `exp`, or a
    non-numeric `exp` to exercise _is_near_expiry's fail-safe branches."""
    import base64
    import json

    payload_b64 = (
        base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    )
    return f"header.{payload_b64}.signature"


def set_token_response(access_token=None, expires_in=3600):
    """Configure the stubbed Keycloak token endpoint to return a token.

    `access_token` may be a short label ("tok-alice") for readability; this
    helper wraps it as a JWT-shaped string with an `exp` claim so the SDK's
    expiry check can decode it. Pass a real `header.payload.signature` to
    override the wrapping."""
    if access_token is None:
        access_token = _jwt_with_exp(expires_in)
    elif access_token.count(".") != 2:
        # Embed the label in a JWT shape so _is_near_expiry can decode the exp.
        access_token = _jwt_with_exp(expires_in)

    response = MagicMock()
    response.json.return_value = {
        "access_token": access_token,
        "expires_in": expires_in,
    }
    response.raise_for_status.return_value = None
    requests_stub.post.return_value = response
    return access_token


def set_hub_auth_state(access_token=None, expires_in=3600, include_token=True):
    """Configure the stubbed JupyterHub `GET /users/<user>` response that the
    Jupyter provider reads the user's Keycloak access_token from.

    Like set_token_response, wraps a label as a JWT-shaped string so the SDK's
    expiry check can decode the `exp`. include_token=False omits access_token
    to drive the missing-token error path."""
    if access_token is None or access_token.count(".") != 2:
        access_token = _jwt_with_exp(expires_in)
    auth_state = {"access_token": access_token} if include_token else {}

    response = MagicMock()
    response.json.return_value = {"name": "alice", "auth_state": auth_state}
    response.raise_for_status.return_value = None
    requests_stub.get.return_value = response
    return access_token
