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


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # Headers and body are two unbuffered writes; without this the body waits
    # on the peer's delayed ACK -- 44ms a reply instead of 0.4ms.
    disable_nagle_algorithm = True
    # Left unset, an idle connection pins its handler thread indefinitely.
    timeout = 10

    def log_message(self, fmt, *args):
        log.debug("http %s", fmt % args)

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

    def response_for(self, path: str) -> tuple[int, bytes, str]:
        if path == "/healthz":
            return 200, b"ok", "text/plain"
        if path == "/readyz":
            if self.server.ready():
                return 200, b"ready", "text/plain"
            return 503, b"tier realm roles not found in the realm", "text/plain"
        if path == "/metrics":
            return 200, self.server.metrics().encode("utf-8"), CONTENT_TYPE_LATEST
        return 404, b"not found", "text/plain"

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        try:
            code, body, content_type = self.response_for(path)
        except Exception:
            log.exception("GET %s failed", path)
            code, body, content_type = 500, b"internal error", "text/plain"
        self.reply(code, body, content_type)


def serve(
    port: int, ready: Callable[[], bool], metrics: Callable[[], str]
) -> ThreadingHTTPServer:
    """Start the listener on a background thread and return it, to stop later."""
    server = ThreadingHTTPServer(("", port), Handler)
    server.ready = ready
    server.metrics = metrics
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("health endpoint on :%s (/healthz, /readyz, /metrics)", port)
    return server
