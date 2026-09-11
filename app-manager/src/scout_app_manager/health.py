"""The pod-IP listener: liveness, readiness, metrics. All read-only.

Bound to every interface, because kubelet probes and Prometheus reach it from
outside the pod. That is what keeps it separate from the reload listener rather
than one server with four routes: everything here is safe for anything that can
route to this pod to ask, and `/reload` is not.

`/healthz` asks whether the process is alive, `/readyz` whether the realm has
been applied. Neither depends on fragment outcomes: a rejected fragment is one
service's problem, not the platform's.
"""

import logging
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .httpd import Handler as BaseHandler
from .metrics import CONTENT_TYPE_LATEST

log = logging.getLogger("app-manager")


def handler_for(
    ready: Callable[[], bool], metrics: Callable[[], str] | None = None
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHandler):
        label = "http"

        def do_GET(self):
            path = self.path_only()
            if path == "/healthz":
                self.reply(200, b"ok")
            elif path == "/readyz":
                if ready():
                    self.reply(200, b"ready")
                else:
                    self.reply(503, b"realm not applied yet")
            elif path == "/metrics" and metrics is not None:
                self.reply(200, metrics().encode("utf-8"), CONTENT_TYPE_LATEST)
            else:
                self.reply(404, b"not found")

    return Handler


def serve(
    port: int, ready: Callable[[], bool], metrics: Callable[[], str] | None = None
) -> ThreadingHTTPServer:
    """Start the listener on a background thread and return it, to stop later."""
    server = ThreadingHTTPServer(("", port), handler_for(ready, metrics))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("health endpoint on :%s (/healthz, /readyz, /metrics)", port)
    return server
