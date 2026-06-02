"""Test harness for the scout SDK (sdk/python/src/scout/).

scout.trino and scout.voila import third-party modules (requests, trino,
voila, jupyter_server) that aren't installed in the unit-test environment -
voila in particular pulls a large dependency tree. Stub them in sys.modules
before the modules are imported so the tests need nothing but pytest.

Run with: cd sdk/python && pytest tests/ -v
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

# requests.auth.AuthBase is the base class scout.trino._DynamicBearerAuth
# extends. Stub it as a plain object so subclasses still work.
requests_auth_stub = _stub("requests.auth")


class _FakeAuthBase:
    pass


requests_auth_stub.AuthBase = _FakeAuthBase
requests_stub.auth = requests_auth_stub


# --- trino.dbapi.connect + trino.auth.Authentication ---
class FakeAuthentication:
    pass


trino_stub = _stub("trino")
trino_dbapi_stub = _stub("trino.dbapi")
trino_dbapi_stub.connect = MagicMock(name="trino.dbapi.connect")
trino_dbapi_stub.Connection = type("Connection", (), {})
trino_auth_stub = _stub("trino.auth")
trino_auth_stub.Authentication = FakeAuthentication
trino_stub.dbapi = trino_dbapi_stub
trino_stub.auth = trino_auth_stub


# --- sqlalchemy: scout.trino.query uses create_engine + text ---
sqlalchemy_stub = _stub("sqlalchemy")
sqlalchemy_stub.create_engine = MagicMock(name="sqlalchemy.create_engine")
sqlalchemy_stub.text = MagicMock(name="sqlalchemy.text", side_effect=lambda s: s)
sqlalchemy_engine_stub = _stub("sqlalchemy.engine")
sqlalchemy_engine_stub.Engine = type("Engine", (), {})
sqlalchemy_stub.engine = sqlalchemy_engine_stub


# --- voila.tornado.handler.TornadoVoilaHandler (scout.voila patches .get) ---
ORIGINAL_GET_RESULT = "voila-original-get-result"


class FakeTornadoVoilaHandler:
    async def get(self, path=None):
        # Module-level constant (not self.<attr>) so the stub still works when
        # called with the duck-typed fake handler the tests pass as `self`.
        return ORIGINAL_GET_RESULT


voila_stub = _stub("voila")
voila_tornado_stub = _stub("voila.tornado")
voila_handler_stub = _stub("voila.tornado.handler")
voila_handler_stub.TornadoVoilaHandler = FakeTornadoVoilaHandler
voila_tornado_stub.handler = voila_handler_stub
voila_stub.tornado = voila_tornado_stub


# --- jupyter_server...AsyncMappingKernelManager (scout.voila subclasses it) ---
class FakeAsyncMappingKernelManager:
    async def start_kernel(self, **kwargs):
        # Echo the kwargs the subclass forwarded so tests can assert on env.
        return {"started_with": kwargs}


js_stub = _stub("jupyter_server")
js_services_stub = _stub("jupyter_server.services")
js_kernels_stub = _stub("jupyter_server.services.kernels")
js_km_stub = _stub("jupyter_server.services.kernels.kernelmanager")
js_km_stub.AsyncMappingKernelManager = FakeAsyncMappingKernelManager
js_kernels_stub.kernelmanager = js_km_stub
js_services_stub.kernels = js_kernels_stub
js_stub.services = js_services_stub


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    """Each test starts with a fresh provider singleton, fresh mocks, and
    the Keycloak/Trino env the SDK expects."""
    from scout import _identity
    from scout import trino as scout_trino

    _identity._singleton = None
    _identity._override_provider = None
    scout_trino._engine = None
    requests_stub.post.reset_mock(return_value=True, side_effect=True)
    requests_stub.get.reset_mock(return_value=True, side_effect=True)
    trino_dbapi_stub.connect.reset_mock(return_value=True, side_effect=True)
    sqlalchemy_stub.create_engine.reset_mock(return_value=True, side_effect=True)

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
    monkeypatch.setenv("TRINO_RW_HOST", "trino-rw.scout-extractor")
    monkeypatch.setenv("TRINO_RW_PORT", "8080")
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
