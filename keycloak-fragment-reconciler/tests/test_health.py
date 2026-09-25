"""The pod-IP listener, driven over a real socket.

The properties that matter here are wire-level -- that a reply carries a status
line at all, and that a broken callback does not hang up on the scraper -- so
every test speaks HTTP to a `serve(0, ...)` on a free port. One server is shared
across the module because tearing one down costs a poll interval.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from http.client import HTTPConnection

import pytest

from scout_keycloak_fragment_reconciler import health
from scout_keycloak_fragment_reconciler.metrics import CONTENT_TYPE_LATEST

RENDERED = "scout_keycloak_fragment_clients 1.0\n"


def boom():
    raise RuntimeError("the reconciler is mid-rebuild")


class Listener:
    """The port, and the two callbacks a test can swap under it."""

    port: int
    ready: Callable[[], bool]
    metrics: Callable[[], str]

    def reset(self) -> None:
        self.ready = lambda: True
        self.metrics = lambda: RENDERED

    def get(self, path: str) -> tuple[int, bytes, str | None]:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.read(), response.getheader("Content-Type")
        finally:
            conn.close()


@pytest.fixture(scope="module")
def _served() -> Iterator[Listener]:
    box = Listener()
    box.reset()
    server = health.serve(0, ready=lambda: box.ready(), metrics=lambda: box.metrics())
    box.port = server.server_address[1]
    try:
        yield box
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def listener(_served) -> Listener:
    _served.reset()
    return _served


class TestTheEndpoints:
    def test_healthz_is_the_process_only(self, listener):
        listener.ready = lambda: False
        assert listener.get("/healthz")[:2] == (200, b"ok")

    def test_readyz_reports_the_tier_roles(self, listener):
        assert listener.get("/readyz")[:2] == (200, b"ready")
        listener.ready = lambda: False
        assert listener.get("/readyz")[:2] == (
            503,
            b"tier realm roles not found in the realm",
        )

    def test_metrics_is_served_in_the_exposition_format(self, listener):
        status, body, content_type = listener.get("/metrics")
        assert (status, body) == (200, RENDERED.encode())
        assert content_type == CONTENT_TYPE_LATEST

    def test_anything_else_is_404(self, listener):
        assert listener.get("/")[0] == 404
        assert listener.get("/readyz/../secrets")[0] == 404

    @pytest.mark.parametrize("path", ["/metrics", "/metrics/", "/metrics?x=1"])
    def test_a_query_string_or_trailing_slash_still_resolves(self, listener, path):
        """Prometheus appends nothing today, but a 404 on `/metrics?x=1` is a
        regression a scrape config could walk into."""
        assert listener.get(path)[:2] == (200, RENDERED.encode())


class TestABrokenCallback:
    """A raising callback must reach the scraper as a status, not as a hangup:
    a dropped connection is a transport error to Prometheus, which cannot be
    alerted on the way a 5xx can."""

    def test_a_raising_metrics_callable_is_a_500(self, listener):
        listener.metrics = boom
        assert listener.get("/metrics")[:2] == (500, b"internal error")

    def test_a_raising_ready_callable_is_an_error_status(self, listener):
        """Not the 503 body, which is a specific claim about the realm."""
        listener.ready = boom
        status, body, _ = listener.get("/readyz")
        assert status >= 500
        assert b"tier realm roles" not in body

    def test_healthz_is_unaffected(self, listener):
        listener.ready = boom
        listener.metrics = boom
        assert listener.get("/healthz")[:2] == (200, b"ok")

    def test_the_listener_survives_to_serve_the_next_request(self, listener):
        listener.ready = boom
        assert listener.get("/readyz")[0] >= 500
        assert listener.get("/healthz")[:2] == (200, b"ok")
        assert listener.get("/readyz")[0] >= 500
