"""Unit tests for voila_runtime — threads the forwarded username into kernels.

The real flow runs the patched handler and the kernel-manager spawn in the same
async task, so the contextvar set by the handler is visible to start_kernel.
The tests run both in one coroutine to reproduce that, and use separate
asyncio.run() calls to confirm requests don't leak identity into each other.
"""

import asyncio
import http.client
import importlib.util
import json
import logging
import time
from types import SimpleNamespace

import jwt
import pytest
import voila_runtime
from conftest import ORIGINAL_GET_RESULT
from cryptography.hazmat.primitives.asymmetric import rsa

HEADER = "X-Auth-Request-Preferred-Username"
KERNEL_ENV_VAR = "X_AUTH_REQUEST_PREFERRED_USERNAME"


def _handler(username):
    """A fake Voila request handler. username=None -> header absent."""
    headers = {} if username is None else {HEADER: username}
    return SimpleNamespace(request=SimpleNamespace(headers=headers))


async def _request_flow(username, base_env=None):
    """Patched handler (sets identity) then a kernel spawn (reads it) — one task."""
    passthrough = await voila_runtime._voila_runtime_get(_handler(username))
    manager = voila_runtime.ScoutMappingKernelManager()
    started = await manager.start_kernel(env=dict(base_env or {}))
    return passthrough, started["started_with"]["env"]


def test_threads_username_into_kernel_env():
    passthrough, env = asyncio.run(_request_flow("alice", {"FOO": "bar"}))
    assert env[KERNEL_ENV_VAR] == "alice"
    assert env["FOO"] == "bar"  # pre-existing kernel env is preserved
    # The handler still delegates to the original Voila get.
    assert passthrough == ORIGINAL_GET_RESULT


def test_absent_header_leaves_kernel_env_clean():
    _, env = asyncio.run(_request_flow(None))
    assert KERNEL_ENV_VAR not in env


def test_empty_header_leaves_kernel_env_clean():
    _, env = asyncio.run(_request_flow(""))
    assert KERNEL_ENV_VAR not in env


def test_identity_does_not_leak_between_requests():
    # Separate asyncio.run() == separate context, like two independent requests.
    _, env_alice = asyncio.run(_request_flow("alice"))
    _, env_bob = asyncio.run(_request_flow("bob"))
    assert env_alice[KERNEL_ENV_VAR] == "alice"
    assert env_bob[KERNEL_ENV_VAR] == "bob"


def test_warns_when_header_missing(caplog):
    with caplog.at_level(logging.WARNING, logger="voila_runtime"):
        asyncio.run(voila_runtime._voila_runtime_get(_handler(None)))
    assert any(
        "X-Auth-Request-Preferred-Username" in r.message
        and r.levelno == logging.WARNING
        for r in caplog.records
    )


def test_no_warning_when_header_present(caplog):
    with caplog.at_level(logging.WARNING, logger="voila_runtime"):
        asyncio.run(voila_runtime._voila_runtime_get(_handler("alice")))
    assert not caplog.records


def test_import_patches_tornado_handler_get():
    # Importing voila_runtime must rebind TornadoVoilaHandler.get (the subclass
    # Tornado actually dispatches GET to). If the patch silently no-ops -- e.g.
    # it targeted the wrong class -- no identity is captured and every Trino
    # query runs as anonymous. The module docstring flags this as the trap.
    from voila.tornado.handler import TornadoVoilaHandler

    assert TornadoVoilaHandler.get is voila_runtime._voila_runtime_get


# --- ALB-OIDC edge: identity from a forwarded, validated access token ---

TOKEN_HEADER = "X-Amzn-Oidc-Accesstoken"
ISSUER = "https://keycloak.test.invalid/realms/scout"
EDGE_CLIENT = "oauth2-proxy"
KEYCLOAK_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(key=KEYCLOAK_KEY, algorithm="RS256", **overrides):
    """An access token as the edge forwards it. A claim overridden to None is dropped."""
    claims = {
        "iss": ISSUER,
        "azp": EDGE_CLIENT,
        "typ": "Bearer",
        "aud": "report-viewer",
        "exp": int(time.time()) + 300,
        "preferred_username": "alice",
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=algorithm)


class FakeJwksClient:
    """Serves KEYCLOAK_KEY's public half as a real PyJWK, or raises `error`."""

    def __init__(self, error=None):
        self.error = error

    def get_signing_key_from_jwt(self, token):
        if self.error:
            raise self.error
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEYCLOAK_KEY.public_key()))
        return jwt.PyJWK(jwk, algorithm="RS256")


@pytest.fixture
def alb_edge(monkeypatch):
    """Switch to the forwarded-token path, with a JWKS that serves KEYCLOAK_KEY."""
    monkeypatch.setattr(voila_runtime, "FORWARDED_TOKEN_HEADER", TOKEN_HEADER)
    monkeypatch.setattr(voila_runtime, "OIDC_ISSUER", ISSUER)
    monkeypatch.setattr(voila_runtime, "OIDC_AUTHORIZED_PARTY", EDGE_CLIENT)
    monkeypatch.setattr(voila_runtime, "_jwks_client", FakeJwksClient())


async def _kernel_username(headers):
    """Run a request with these headers, then a kernel spawn; return its username."""
    handler = SimpleNamespace(request=SimpleNamespace(headers=headers))
    await voila_runtime._voila_runtime_get(handler)
    started = await voila_runtime.ScoutMappingKernelManager().start_kernel(env={})
    return started["started_with"]["env"].get(KERNEL_ENV_VAR)


def test_alb_valid_token_threads_preferred_username(alb_edge):
    assert asyncio.run(_kernel_username({TOKEN_HEADER: _token()})) == "alice"


def test_alb_tolerates_clock_skew(alb_edge):
    token = _token(iat=int(time.time()) + 5)  # Keycloak's clock slightly ahead
    assert asyncio.run(_kernel_username({TOKEN_HEADER: token})) == "alice"


# Tokens are built inside the test, so a slow collection can't expire them all
# and let the issuer/azp cases pass for the wrong reason.
@pytest.mark.parametrize(
    "make_token",
    [
        lambda: _token(exp=int(time.time()) - 120),
        lambda: _token(exp=None),
        lambda: _token(iss="https://other.test.invalid/realms/scout"),
        lambda: _token(azp="jupyterhub"),
        lambda: _token(azp=None),
        lambda: _token(typ="ID"),
        lambda: _token(
            key=rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ),
        lambda: _token(key="x" * 32, algorithm="HS256"),
        lambda: _token(key=None, algorithm="none"),
        lambda: "not-a-jwt",
    ],
    ids=[
        "expired",
        "no-exp",
        "other-issuer",
        "other-client",
        "no-azp",
        "id-token",
        "wrong-key",
        "hs256",
        "alg-none",
        "garbage",
    ],
)
def test_alb_rejects_invalid_token(alb_edge, make_token):
    assert asyncio.run(_kernel_username({TOKEN_HEADER: make_token()})) is None


@pytest.mark.parametrize(
    "error",
    [
        jwt.exceptions.PyJWKClientConnectionError("JWKS unreachable"),
        ValueError("JWKS response is not JSON"),
        OSError("connection reset"),
        http.client.RemoteDisconnected("closed"),
    ],
    ids=["connection", "not-json", "os-error", "disconnected"],
)
def test_alb_jwks_failure_runs_as_anonymous(alb_edge, monkeypatch, error):
    monkeypatch.setattr(voila_runtime, "_jwks_client", FakeJwksClient(error))
    assert asyncio.run(_kernel_username({TOKEN_HEADER: _token()})) is None


def test_alb_ignores_oauth2_proxy_header(alb_edge):
    # Nothing at the ALB edge strips this header, so it must not count.
    username = asyncio.run(
        _kernel_username({HEADER: "mallory", TOKEN_HEADER: _token(azp="jupyterhub")})
    )
    assert username is None
    assert asyncio.run(_kernel_username({HEADER: "mallory"})) is None


def test_alb_warns_when_token_missing(alb_edge, caplog):
    with caplog.at_level(logging.WARNING, logger="voila_runtime"):
        asyncio.run(_kernel_username({}))
    assert any(TOKEN_HEADER in r.message for r in caplog.records)


def test_alb_warns_when_preferred_username_missing(alb_edge, caplog):
    token = _token(preferred_username=None)
    with caplog.at_level(logging.WARNING, logger="voila_runtime"):
        assert asyncio.run(_kernel_username({TOKEN_HEADER: token})) is None
    assert any("preferred_username" in r.message for r in caplog.records)


def test_forwarded_header_without_settings_fails_import(monkeypatch):
    monkeypatch.setenv("VOILA_FORWARDED_TOKEN_HEADER", TOKEN_HEADER)
    for name in (
        "VOILA_OIDC_ISSUER",
        "VOILA_OIDC_JWKS_URL",
        "VOILA_OIDC_AUTHORIZED_PARTY",
    ):
        monkeypatch.delenv(name, raising=False)
    # A fresh copy of the module: the guard raises before the handler patch,
    # so the already-imported module's patch is left in place.
    spec = importlib.util.spec_from_file_location(
        "voila_runtime_guard", voila_runtime.__file__
    )
    with pytest.raises(RuntimeError, match="VOILA_OIDC_ISSUER"):
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
    from voila.tornado.handler import TornadoVoilaHandler

    assert TornadoVoilaHandler.get is voila_runtime._voila_runtime_get
