"""The Kubernetes client's plumbing: error classification.

Drives a fake `_http` rather than an API server, but the responses are real
`httpx.Response` objects, so `.json()`, `.content` and `.text` behave exactly as
they do in production -- which is the point, since what is under test is how
this module reacts to those. The service-account token is a real file in
`tmp_path`, for the same reason.

Every handler downstream of here is narrow: `take_snapshot`, `_read_secret` and
`emit_event` all catch `ApiError` and nothing wider, so a failure this module
leaves unclassified ends the whole pass instead of the one fragment.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import httpx2 as httpx
import pytest

from scout_keycloak_fragment_reconciler import k8s
from scout_keycloak_fragment_reconciler.core import Reconciler
from scout_keycloak_fragment_reconciler.k8s import ApiError, Client

SELECTOR = "keycloak.scout.xnat.org/fragment=true"
TOKEN = "sa-token"


class Sent(NamedTuple):
    method: str
    url: str
    json: object
    headers: dict


class FakeHttp:
    """Scripted responses, and a log of every request that produced one.

    `responses` is consumed in order; an exhausted script answers 200 with an
    empty object, so a test only has to spell out the responses it cares about.
    """

    def __init__(self, *, responses: list | None = None) -> None:
        self.responses = list(responses or [])
        self.requests: list[Sent] = []

    def request(
        self,
        method: str,
        url: str,
        json: object = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        self.requests.append(Sent(method, url, json, dict(headers or {})))
        if not self.responses:
            return httpx.Response(200, json={})
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


@pytest.fixture
def token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The token as the pod sees it: a file in a projected volume."""
    path = tmp_path / "token"
    path.write_text(TOKEN, encoding="utf-8")
    monkeypatch.setattr(k8s, "TOKEN_PATH", path)
    return path


@pytest.fixture
def client(token: Path) -> Callable[[FakeHttp], Client]:
    def build(http: FakeHttp) -> Client:
        instance = Client()
        instance._http = http
        return instance

    return build


# --- the token ----------------------------------------------------------


def test_a_rotated_token_reaches_the_next_request(client, token):
    http = FakeHttp()
    instance = client(http)
    instance.request("GET", "/api/v1/configmaps")
    token.write_text("rotated", encoding="utf-8")
    instance.request("GET", "/api/v1/configmaps")
    assert [sent.headers["Authorization"] for sent in http.requests] == [
        f"Bearer {TOKEN}",
        "Bearer rotated",
    ]


def test_an_unreadable_token_is_an_api_error(client, token):
    """An `OSError` is no `httpx.HTTPError`, so unclassified it would escape
    every handler between here and the run loop."""
    token.unlink()
    with pytest.raises(ApiError) as raised:
        client(FakeHttp()).request("GET", "/api/v1/configmaps")
    assert raised.value.status == 0, "the request never reached an answer"


# --- error classification -----------------------------------------------


def test_a_non_json_body_is_an_api_error(client):
    """A 2xx carrying HTML -- what a proxy in front of the API server answers.

    The decode has to be classified like any other failure, because a bare
    JSONDecodeError is a ValueError and escapes every handler between here and
    the run loop, taking the rest of the pass with it.
    """
    http = FakeHttp(responses=[httpx.Response(200, content=b"<html/>")])
    with pytest.raises(ApiError) as raised:
        client(http).list_configmaps(SELECTOR)
    assert raised.value.status == 200, "a garbled answer is still an answer"


def test_a_transport_failure_is_status_zero(client):
    http = FakeHttp(responses=[httpx.ConnectError("no route")])
    with pytest.raises(ApiError) as raised:
        client(http).list_configmaps(SELECTOR)
    assert raised.value.status == 0


def test_an_empty_body_is_not_a_decode_failure(client):
    http = FakeHttp(responses=[httpx.Response(201)])
    assert client(http).request("POST", "/api/v1/namespaces/demo/events") == {}


# --- events -------------------------------------------------------------

INVOLVED = {"metadata": {"namespace": "demo", "name": "hello-keycloak"}}


def test_an_emitted_event_answers_its_name(client):
    http = FakeHttp(
        responses=[httpx.Response(201, json={"metadata": {"name": "hello.abc"}})]
    )
    name = client(http).emit_event(
        involved=INVOLVED, reason="FragmentInvalid", message="m", timestamp="t"
    )
    assert name == "hello.abc"


def test_a_lost_event_answers_none(client):
    http = FakeHttp(responses=[httpx.Response(403, text="forbidden")])
    name = client(http).emit_event(
        involved=INVOLVED, reason="FragmentInvalid", message="m", timestamp="t"
    )
    assert name is None


def test_a_repeat_is_a_merge_patch_of_count_and_last_timestamp(client):
    http = FakeHttp()
    assert client(http).repeat_event(
        involved=INVOLVED, name="hello.abc", count=4, timestamp="t"
    )
    sent = http.requests[0]
    assert sent.method == "PATCH"
    assert sent.url.endswith("/api/v1/namespaces/demo/events/hello.abc")
    assert sent.headers["Content-Type"] == "application/merge-patch+json"
    assert sent.json == {"count": 4, "lastTimestamp": "t"}


def test_a_repeat_of_an_expired_event_did_not_land(client):
    http = FakeHttp(responses=[httpx.Response(404, text="not found")])
    assert not client(http).repeat_event(
        involved=INVOLVED, name="hello.abc", count=2, timestamp="t"
    )


# --- what an unclassified failure would cost -----------------------------


def test_a_garbled_list_leaves_the_snapshot_incomplete(settings, kc, client):
    """GC may only infer an orphan from a LIST that completed.

    An escaping decode failure would end the pass at `run_forever`'s blanket
    handler instead, leaving the previous pass's snapshot serving metrics and
    reporting while `/readyz` stays ready.
    """
    http = FakeHttp(responses=[httpx.Response(200, content=b"<html/>")])
    snapshot = Reconciler(settings, client(http), kc).take_snapshot()
    assert not snapshot.complete
    assert snapshot.claims == {}
