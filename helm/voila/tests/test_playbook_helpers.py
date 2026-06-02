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
    assert kwargs["host"] == "trino-rw.scout-extractor"
    assert kwargs["port"] == 8080
    assert kwargs["http_scheme"] == "http"
    assert kwargs["user"] == "carol"
    # trino-rw is unauthenticated inside its NetworkPolicy boundary: no JWT mint.
    assert "auth" not in kwargs


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
