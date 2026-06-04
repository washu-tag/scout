"""Unit tests for voila_runtime — threads the forwarded username into kernels.

The real flow runs the patched handler and the kernel-manager spawn in the same
async task, so the contextvar set by the handler is visible to start_kernel.
The tests run both in one coroutine to reproduce that, and use separate
asyncio.run() calls to confirm requests don't leak identity into each other.
"""

import asyncio
import logging
from types import SimpleNamespace

import voila_runtime
from conftest import ORIGINAL_GET_RESULT

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
