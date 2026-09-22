"""The admin client's plumbing: token caching, error classification, retry.

Everything here drives a fake `_http` rather than a server, but the responses
are real `httpx.Response` objects, so `.json()`, `.content`, `.text` and
`.headers` behave exactly as they do in production -- which is the point, since
what is under test is how this module reacts to those.

The clock is never monkeypatched. Expiries are asserted against a
`time.monotonic()` reading taken in the test, or against each other.
"""

from __future__ import annotations

import itertools
import time
from typing import NamedTuple

import httpx2 as httpx
import pytest

from scout_keycloak_fragment_reconciler.keycloak import Admin, KeycloakError

BASE = "http://keycloak:8080"
TOKEN_URL = f"{BASE}/realms/scout/protocol/openid-connect/token"


class Sent(NamedTuple):
    method: str
    url: str
    json: object
    headers: dict


class FakeHttp:
    """Scripted responses, and a log of every request that produced one.

    `responses` is consumed in order; an exhausted script answers 200 with an
    empty list, so a test only has to spell out the responses it cares about.
    """

    def __init__(
        self,
        *,
        expires_in: float = 300,
        responses: list | None = None,
        token_responses: list | None = None,
    ) -> None:
        self.expires_in = expires_in
        self.responses = list(responses or [])
        self.token_responses = list(token_responses or [])
        self.token_posts: list[dict] = []
        self.requests: list[Sent] = []
        self._minted = itertools.count(1)

    def post(self, url: str, data: dict | None = None) -> httpx.Response:
        self.token_posts.append({"url": url, "data": data or {}})
        if self.token_responses:
            return self.token_responses.pop(0)
        return httpx.Response(
            200,
            json={
                "access_token": f"tok-{next(self._minted)}",
                "expires_in": self.expires_in,
            },
        )

    def request(
        self,
        method: str,
        url: str,
        json: object = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        self.requests.append(Sent(method, url, json, dict(headers or {})))
        if not self.responses:
            return httpx.Response(200, json=[])
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def admin(http: FakeHttp) -> Admin:
    client = Admin(BASE, "scout", "fragment_reconciler_svc", "secret")
    client._http = http
    return client


# --- token cache --------------------------------------------------------


@pytest.mark.parametrize("expires_in", [300, 30, 5, 0])
def test_expiry_never_lands_in_the_past(expires_in):
    http = FakeHttp(expires_in=expires_in)
    client = admin(http)
    before = time.monotonic()
    client._access_token()
    assert client._expires_at >= before


def test_a_live_token_is_reused():
    http = FakeHttp(expires_in=300)
    client = admin(http)
    assert client._access_token() == client._access_token()
    assert len(http.token_posts) == 1


def test_the_refresh_margin_scales_with_the_lifetime():
    """A tenfold lifetime buys a tenfold cache window (ADR 0024).

    A margin that is a fraction of the lifetime holds for any lifespan; a
    constant one has to be chosen against a lifespan nobody here controls.
    """
    short = admin(FakeHttp(expires_in=300))
    long = admin(FakeHttp(expires_in=3000))
    before = time.monotonic()
    short._access_token()
    long._access_token()
    assert long._expires_at - before == pytest.approx(
        10 * (short._expires_at - before), rel=1e-3
    )


@pytest.mark.parametrize("expires_in", [30, 5])
def test_a_short_lived_token_is_still_cached(expires_in):
    http = FakeHttp(expires_in=expires_in)
    client = admin(http)
    client._access_token()
    client._access_token()
    assert len(http.token_posts) == 1


def test_force_re_authenticates():
    http = FakeHttp(expires_in=300)
    client = admin(http)
    first = client._access_token()
    assert client._access_token(force=True) != first
    assert len(http.token_posts) == 2


# --- error classification -----------------------------------------------


def test_a_non_json_body_is_a_keycloak_error():
    """A 2xx carrying HTML -- what an ingress in front of Keycloak answers.

    The decode has to be classified like any other failure, because a bare
    JSONDecodeError is a ValueError and escapes every handler between here and
    the run loop, taking the rest of the pass with it.
    """
    http = FakeHttp(responses=[httpx.Response(200, content=b"<html/>")])
    with pytest.raises(KeycloakError):
        admin(http).list_clients()


def test_a_non_json_token_body_is_a_keycloak_error():
    http = FakeHttp(token_responses=[httpx.Response(200, content=b"<html/>")])
    with pytest.raises(KeycloakError):
        admin(http)._access_token()
