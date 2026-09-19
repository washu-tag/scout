"""The pod-IP listener: liveness, readiness, metrics. All read-only.

The split matters. `/readyz` means the tier roles exist and Keycloak is
reachable, so a service that could only create clients nothing can reach does
not report healthy. `/healthz` asks only whether the process is up, so a
Keycloak outage does not crashloop the pod -- an outage here is only ever a
delay.
"""

import logging
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .metrics import CONTENT_TYPE_LATEST

log = logging.getLogger("keycloak-fragment-reconciler")

# Nothing here reads a body; this bounds what we swallow before hanging up.
MAX_BODY_BYTES = 1 << 20
_CHUNK_BYTES = 65536


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # Headers and body are two unbuffered writes; without this the body waits
    # on the peer's delayed ACK -- 44ms a reply instead of 0.4ms.
    disable_nagle_algorithm = True
    # Left unset, an idle connection pins its handler thread indefinitely.
    timeout = 10

    def log_message(self, fmt, *args):
        log.debug("http %s", fmt % args)

    def path_only(self) -> str:
        return self.path.split("?")[0].rstrip("/") or "/"

    def reply(
        self, code: int, body: bytes = b"", content_type: str = "text/plain"
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def drain(self) -> None:
        """Consume the request body, or hang up rather than mis-frame the next.

        Keep-alive connections, so an unread body is parsed as the next request
        line.
        """
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            self.close_connection = True
            return
        try:
            remaining = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            return
        if remaining > MAX_BODY_BYTES:
            self.close_connection = True
            return
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, _CHUNK_BYTES))
            if not chunk:
                self.close_connection = True
                return
            remaining -= len(chunk)


def handler_for(
    ready: Callable[[], bool], metrics: Callable[[], str]
) -> type[BaseHTTPRequestHandler]:
    class _Handler(Handler):
        def do_GET(self):
            self.drain()
            path = self.path_only()
            if path == "/healthz":
                self.reply(200, b"ok")
            elif path == "/readyz":
                if ready():
                    self.reply(200, b"ready")
                else:
                    self.reply(503, b"tier realm roles not found in the realm")
            elif path == "/metrics":
                self.reply(200, metrics().encode("utf-8"), CONTENT_TYPE_LATEST)
            else:
                self.reply(404, b"not found")

    return _Handler


def serve(
    port: int, ready: Callable[[], bool], metrics: Callable[[], str]
) -> ThreadingHTTPServer:
    """Start the listener on a background thread and return it, to stop later."""
    server = ThreadingHTTPServer(("", port), handler_for(ready, metrics))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("health endpoint on :%s (/healthz, /readyz, /metrics)", port)
    return server
