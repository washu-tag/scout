"""Test harness for the chart-shipped Voila helpers (helm/voila/files/).

voila_runtime and playbook_helpers import third-party modules
(voila, jupyter_server, trino) that aren't installed in the unit-test
environment - voila in particular pulls a large dependency tree. Stub
them in sys.modules before the helpers are imported so the tests need
nothing but pytest.

Run with: cd helm/voila && PYTHONPATH=files pytest tests/ -v
"""

import os
import sys
import types
from unittest.mock import MagicMock

import pytest

# Make voila_runtime / playbook_helpers importable even without
# PYTHONPATH=files (so the tests work from inside an IDE that runs them
# from the tests dir directly).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "files"))


def _stub(name):
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


# --- trino.dbapi.connect ---
trino_stub = _stub("trino")
trino_dbapi_stub = _stub("trino.dbapi")
trino_dbapi_stub.connect = MagicMock(name="trino.dbapi.connect")
trino_dbapi_stub.Connection = type("Connection", (), {})
trino_stub.dbapi = trino_dbapi_stub


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


# --- scout._identity.resolve_audit_user (playbook_helpers depends on it) ---
# Stubbed as a real function reading the env, mirroring what the SDK does.
scout_stub = _stub("scout")
scout_identity_stub = _stub("scout._identity")


def _stub_resolve_audit_user():
    voila_user = os.environ.get("X_AUTH_REQUEST_PREFERRED_USERNAME", "")
    if voila_user:
        return voila_user
    hub_user = os.environ.get("JUPYTERHUB_USER")
    if hub_user:
        return hub_user
    return "anonymous"


scout_identity_stub.resolve_audit_user = _stub_resolve_audit_user
scout_stub._identity = scout_identity_stub


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    """Each test starts with fresh mocks and the Trino env the helpers expect."""
    trino_dbapi_stub.connect.reset_mock(return_value=True, side_effect=True)
    monkeypatch.delenv("X_AUTH_REQUEST_PREFERRED_USERNAME", raising=False)
    monkeypatch.delenv("JUPYTERHUB_USER", raising=False)
    monkeypatch.setenv("TRINO_RW_HOST", "trino-rw.scout-extractor")
    monkeypatch.setenv("TRINO_RW_PORT", "8080")
    monkeypatch.setenv("TRINO_CATALOG", "delta")
    monkeypatch.setenv("TRINO_SCHEMA", "default")
    yield
