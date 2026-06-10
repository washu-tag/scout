"""Unit tests for playbook_helpers - chart-shipped helpers for Voila playbook code.

connect_writeback() targets the unauthenticated trino-rw instance for
reviewer-annotation writes; identity is best-effort and fails OPEN.
"""

import playbook_helpers

from conftest import trino_dbapi_stub


def _connect_kwargs():
    return trino_dbapi_stub.connect.call_args.kwargs


def test_connect_writeback_targets_unauthenticated_rw_instance(monkeypatch):
    monkeypatch.setenv("X_AUTH_REQUEST_PREFERRED_USERNAME", "carol")

    playbook_helpers.connect_writeback()

    kwargs = _connect_kwargs()
    # The conftest sets these to non-default sentinels, so matching them proves
    # connect_writeback() reads the env rather than hardcoding the defaults.
    assert kwargs["host"] == "rw-host.test.invalid"
    assert kwargs["port"] == 9090  # str env -> int()
    assert kwargs["catalog"] == "delta_test"
    assert kwargs["schema"] == "schema_test"
    assert kwargs["http_scheme"] == "http"
    assert kwargs["user"] == "carol"
    # trino-rw is unauthenticated inside its NetworkPolicy boundary: no JWT mint.
    assert "auth" not in kwargs


def test_connect_writeback_falls_back_to_defaults_when_env_unset(monkeypatch):
    # With the TRINO_* env unset, connect_writeback() must use the in-cluster
    # defaults (and still int()-cast the default port string).
    for var in ("TRINO_RW_HOST", "TRINO_RW_PORT", "TRINO_CATALOG", "TRINO_SCHEMA"):
        monkeypatch.delenv(var, raising=False)

    playbook_helpers.connect_writeback()

    kwargs = _connect_kwargs()
    assert kwargs["host"] == "trino-rw.scout-extractor"
    assert kwargs["port"] == 8080
    assert kwargs["catalog"] == "delta"
    assert kwargs["schema"] == "default"


def test_connect_writeback_audit_user_falls_back_to_anonymous(monkeypatch):
    # Fail-OPEN: a missing identity must not block a reviewer's annotation save.
    playbook_helpers.connect_writeback()

    assert _connect_kwargs()["user"] == "anonymous"


def test_connect_writeback_uses_hub_user_when_no_voila_user(monkeypatch):
    # Edge case: somehow running in a non-Voila context but JUPYTERHUB_USER is set.
    # Still records who, even though the network connection would actually fail.
    monkeypatch.setenv("JUPYTERHUB_USER", "hub-user")

    playbook_helpers.connect_writeback()

    assert _connect_kwargs()["user"] == "hub-user"
