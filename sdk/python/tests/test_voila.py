"""Unit tests for scout.voila - threads the forwarded username into kernels,
and hosts connect_rw for the Voila-only write path.

The real handler + kernel-spawn flow runs in the same async task, so the
contextvar set by the handler is visible to start_kernel. The tests run
both in one coroutine to reproduce that, and use separate asyncio.run()
calls to confirm requests don't leak identity into each other.
"""

import asyncio
import logging
from types import SimpleNamespace

from scout import voila as scout_voila

from conftest import ORIGINAL_GET_RESULT, requests_stub, trino_dbapi_stub

HEADER = "X-Auth-Request-Preferred-Username"
KERNEL_ENV_VAR = "X_AUTH_REQUEST_PREFERRED_USERNAME"


def _handler(username):
    """A fake Voila request handler. username=None -> header absent."""
    headers = {} if username is None else {HEADER: username}
    return SimpleNamespace(request=SimpleNamespace(headers=headers))


async def _request_flow(username, base_env=None):
    """Patched handler (sets identity) then a kernel spawn (reads it) - one task."""
    passthrough = await scout_voila._scout_voila_get(_handler(username))
    manager = scout_voila.ScoutMappingKernelManager()
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
    with caplog.at_level(logging.WARNING, logger="scout.voila"):
        asyncio.run(scout_voila._scout_voila_get(_handler(None)))
    assert any(
        "X-Auth-Request-Preferred-Username" in r.message
        and r.levelno == logging.WARNING
        for r in caplog.records
    )


def test_no_warning_when_header_present(caplog):
    with caplog.at_level(logging.WARNING, logger="scout.voila"):
        asyncio.run(scout_voila._scout_voila_get(_handler("alice")))
    assert not caplog.records


# --- connect_rw ----------------------------------------------------------


def _connect_kwargs():
    return trino_dbapi_stub.connect.call_args.kwargs


def test_connect_rw_targets_unauthenticated_rw_instance(monkeypatch):
    monkeypatch.setenv("X_AUTH_REQUEST_PREFERRED_USERNAME", "carol")

    scout_voila.connect_rw()

    kwargs = _connect_kwargs()
    assert kwargs["host"] == "trino-rw.scout-extractor"
    assert kwargs["port"] == 8080
    assert kwargs["http_scheme"] == "http"
    assert kwargs["user"] == "carol"
    # trino-rw is unauthenticated inside its NetworkPolicy boundary: no JWT mint.
    assert "auth" not in kwargs
    assert requests_stub.post.call_count == 0


def test_connect_rw_audit_user_falls_back_to_anonymous(monkeypatch):
    # Fail-OPEN: a missing identity must not block a reviewer's annotation save.
    scout_voila.connect_rw()

    assert _connect_kwargs()["user"] == "anonymous"
