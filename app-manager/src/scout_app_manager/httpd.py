"""What the two listeners share: HTTP/1.1 framing, one reply shape, no framework.

`reload` (loopback, one POST) and `health` (every interface, probes and
metrics) are separate servers because they differ in what may reach them, not
in what they serve. Everything below that -- the reply headers, the request
logging, the keep-alive body handling -- is the same for both and lives here
rather than in two copies.

No framework, because there is nothing here to route: four paths, no request
bodies to parse, no response models, and no second concurrency model wanted in
a process that is otherwise threads and blocking calls.
"""

import logging
from http.server import BaseHTTPRequestHandler

log = logging.getLogger("app-manager")

# Nothing we serve reads a request body; this is only a bound on what we are
# willing to swallow before hanging up instead.
MAX_BODY_BYTES = 1 << 20
_CHUNK_BYTES = 65536


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # Headers and body are two unbuffered writes; without this the body waits on
    # the peer's delayed ACK -- 44ms a reply instead of 0.4ms.
    disable_nagle_algorithm = True
    # Left unset, an idle connection pins its handler thread indefinitely.
    timeout = 10
    label = "http"

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.label, fmt % args)

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
        """Consume the request body, or hang up rather than mis-frame the next one.

        These are keep-alive connections, so an unread body is parsed as the
        next request line: 400s, a socket that hangs until the client's own
        timeout, and the notification it carried dropped. The discovery sidecar
        sends a body whenever REQ_PAYLOAD is set.
        """
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            # Not decoded at this layer; a fresh connection is the only
            # framing we can be sure of.
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
