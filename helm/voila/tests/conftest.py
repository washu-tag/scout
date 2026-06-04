"""Test harness for the Voila Trino helpers (helm/voila/files/).

scout_trino and voila_runtime import third-party modules (requests, trino, voila,
jupyter_server) that aren't installed in the unit-test environment — voila in
particular pulls a large dependency tree. Stub them in sys.modules before the
helpers are imported so the tests need nothing but pytest.

Run with: cd helm/voila && PYTHONPATH=files pytest tests/ -v
"""

import os
import sys
import types
from unittest.mock import MagicMock

import pytest

# Make scout_trino / voila_runtime importable even without PYTHONPATH=files.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "files"))


def _stub(name):
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


# --- requests: scout_trino.requests.post mints the voila_svc token ---
requests_stub = _stub("requests")
requests_stub.post = MagicMock(name="requests.post")


# --- trino.dbapi.connect + trino.auth.JWTAuthentication ---
class FakeJWTAuthentication:
    """Stand-in for trino.auth.JWTAuthentication; compares by token so tests
    can assert the right token was attached."""

    def __init__(self, token):
        self.token = token

    def __eq__(self, other):
        return isinstance(other, FakeJWTAuthentication) and other.token == self.token

    def __repr__(self):
        return f"FakeJWTAuthentication({self.token!r})"


trino_stub = _stub("trino")
trino_dbapi_stub = _stub("trino.dbapi")
trino_dbapi_stub.connect = MagicMock(name="trino.dbapi.connect")
trino_auth_stub = _stub("trino.auth")
trino_auth_stub.JWTAuthentication = FakeJWTAuthentication
trino_stub.dbapi = trino_dbapi_stub
trino_stub.auth = trino_auth_stub


# --- voila.tornado.handler.TornadoVoilaHandler (voila_runtime patches .get) ---
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


# --- jupyter_server...AsyncMappingKernelManager (voila_runtime subclasses it) ---
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
    """Each test starts with a clean token cache, fresh mocks, and the
    Keycloak/Trino env the helpers expect."""
    import scout_trino

    scout_trino._TOKEN_CACHE["access_token"] = None
    scout_trino._TOKEN_CACHE["expires_at"] = 0.0
    requests_stub.post.reset_mock(return_value=True, side_effect=True)
    trino_dbapi_stub.connect.reset_mock(return_value=True, side_effect=True)

    monkeypatch.delenv("X_AUTH_REQUEST_PREFERRED_USERNAME", raising=False)
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


def set_token_response(access_token="tok-abc", expires_in=3600):
    """Configure the stubbed Keycloak token endpoint to return a token."""
    response = MagicMock()
    response.json.return_value = {
        "access_token": access_token,
        "expires_in": expires_in,
    }
    response.raise_for_status.return_value = None
    requests_stub.post.return_value = response
