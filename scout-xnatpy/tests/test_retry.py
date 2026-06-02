"""Tests for the 401-refresh hook installed on xnatpy's requests.Session."""

from __future__ import annotations

import requests

from scout_xnat import retry
from scout_xnat.errors import ScoutXnatAuthError


class FakeInterface:
    """Stand-in for xnatpy's underlying requests.Session."""

    def __init__(self, send_result=None):
        # A real cookie jar: the hook calls prepare_cookies(), which iterates it.
        self.cookies = requests.cookies.RequestsCookieJar()
        self.hooks = {}
        self.sent = []
        self.send_result = send_result

    def send(self, request, **kwargs):
        self.sent.append((request, kwargs))
        return self.send_result


class FakeConnection:
    def __init__(self, interface):
        self.interface = interface


def _make_response(status_code):
    resp = requests.Response()
    resp.status_code = status_code
    prepared = requests.Request(
        method="GET", url="http://xnat.local/data/projects"
    ).prepare()
    prepared.headers["Cookie"] = "JSESSIONID=stale"
    resp.request = prepared
    return resp


def _install(interface, token_provider, mint, verify=True):
    """Install the hook and return the registered on_response callable."""
    retry.install_refresh_hook(
        FakeConnection(interface),
        "http://xnat.local",
        token_provider,
        mint,
        verify=verify,
    )
    return interface.hooks["response"][-1]


def test_non_401_is_a_noop():
    interface = FakeInterface()
    calls = []
    hook = _install(interface, lambda: calls.append("token") or "t", lambda *a: "j")

    result = hook(_make_response(200))

    assert result is None
    assert calls == []  # token provider not consulted
    assert interface.sent == []  # no retry sent


def test_401_remints_and_retries_once():
    sentinel = object()
    interface = FakeInterface(send_result=sentinel)
    mint_calls = []

    def mint(server, token, verify):
        mint_calls.append((server, token, verify))
        return "fresh-jsession"

    hook = _install(interface, lambda: "fresh-token", mint, verify="ca.pem")

    result = hook(_make_response(401))

    # Re-minted with the fresh token and the configured verify setting.
    assert mint_calls == [("http://xnat.local", "fresh-token", "ca.pem")]
    # New JSESSIONID swapped onto the session.
    assert interface.cookies.get("JSESSIONID") == "fresh-jsession"
    # Original request re-sent exactly once, and its response returned.
    assert len(interface.sent) == 1
    assert result is sentinel
    # Re-entrancy guard cleared afterward.
    assert getattr(retry._retry_state, "active", False) is False


def test_reentrancy_guard_blocks_nested_retry():
    interface = FakeInterface()

    def token_provider():
        raise AssertionError(
            "token provider must not be called while a retry is active"
        )

    hook = _install(interface, token_provider, lambda *a: "j")

    retry._retry_state.active = True
    try:
        result = hook(_make_response(401))
    finally:
        retry._retry_state.active = False

    assert result is None
    assert interface.sent == []


def test_auth_error_during_reauth_is_swallowed():
    interface = FakeInterface()

    def token_provider():
        raise ScoutXnatAuthError("hub unreachable")

    hook = _install(interface, token_provider, lambda *a: "j")

    result = hook(_make_response(401))

    # Original 401 response left to flow through; no retry attempted.
    assert result is None
    assert interface.sent == []
    assert getattr(retry._retry_state, "active", False) is False
