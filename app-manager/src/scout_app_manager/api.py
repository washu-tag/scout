"""
Exposes POST /reload API endpoint to the sidecar's REQ_URL,
so new files can cause immediate realm refresh.
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


def serve(port: int, wake: threading.Event) -> None:
    log.info("reload endpoint on %s:%s%s", LOOPBACK, port, RELOAD_PATH)
    ThreadingHTTPServer((LOOPBACK, port), handler_for(wake)).serve_forever()
