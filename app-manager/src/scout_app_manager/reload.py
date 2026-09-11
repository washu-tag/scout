"""The loopback listener: POST /reload, the discovery sidecar's doorbell.

Its own server rather than a route on the health listener, because the two
differ in the one thing a route cannot express -- what may reach them. A POST
here triggers a realm write, so it binds 127.0.0.1 and the sidecar, which
shares this pod's network namespace, is the only thing that can call it. The
health listener binds every interface for kubelet and Prometheus. Merging them
would publish the write trigger to anything that can route to the pod.
"""

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .httpd import Handler as BaseHandler

log = logging.getLogger("app-manager")

LOOPBACK = "127.0.0.1"
RELOAD_PATH = "/reload"


def handler_for(wake: threading.Event) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHandler):
        label = "reload"

        def do_POST(self):
            # Before replying: an unread body would be read as the next
            # request on this keep-alive connection.
            self.drain()
            if self.path_only() != RELOAD_PATH:
                self.reply(404, b"not found")
                return
            log.info("RELOAD    discovery reported a change")
            wake.set()
            self.reply(202, b"queued")

        def do_GET(self):
            # 405 rather than silence, so a misconfigured REQ_METHOD is visible.
            self.reply(405, b"POST " + RELOAD_PATH.encode())

    return Handler


def serve(port: int, wake: threading.Event) -> ThreadingHTTPServer:
    """Start the listener on a background thread and return it, to stop later."""
    server = ThreadingHTTPServer((LOOPBACK, port), handler_for(wake))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("reload endpoint on %s:%s%s", LOOPBACK, port, RELOAD_PATH)
    return server
