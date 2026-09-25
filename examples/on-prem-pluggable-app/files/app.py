"""The whole service: one page, standard library only, no dependencies.

Replace this with your real app. The chart around it is the part worth copying.
"""

import html
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8080"))

# oauth2-proxy sets this at the ingress (ADR 0022). It is worth trusting only
# because the edge gate is the sole route to this pod.
USER_HEADER = "X-Auth-Request-Preferred-Username"

log = logging.getLogger("example-app")

PAGE = """<!doctype html>
<meta charset="utf-8"><title>Example Pluggable App</title>
<style>
body {{ font: 15px/1.7 ui-sans-serif, system-ui, sans-serif; max-width: 40rem;
       margin: 4rem auto; padding: 0 1rem; color: #222 }}
dt {{ font-weight: 600; margin-top: 1rem }}
code {{ background: #f2f2f2; padding: .1rem .3rem; border-radius: 3px }}
</style>
<h1>Example Pluggable App</h1>
<p>This app's Helm chart registered its own launchpad chip and its own Keycloak
client. Nothing in the Scout base realm mentions it.</p>
<dl>
  <dt>Signed in as</dt>
  <dd><code>{user}</code>, from the <code>{header}</code> header the
      oauth2-proxy edge gate put on this request</dd>
</dl>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            body, content_type = b"ok", "text/plain; charset=utf-8"
        else:
            user = self.headers.get(USER_HEADER)
            if user is None:
                log.warning("%s requested without %s header", self.path, USER_HEADER)
            body = PAGE.format(
                user=html.escape(user or "(header absent)"),
                header=USER_HEADER,
            ).encode()
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_request(self, code="-", size="-"):
        level = logging.DEBUG if self.path == "/healthz" else logging.INFO
        user = self.headers.get(USER_HEADER, "-")
        log.log(level, '"%s" %s user=%s', self.requestline, code, user)

    def log_message(self, format, *args):  # noqa: A002
        log.warning("%s - %s", self.address_string(), format % args)


if __name__ == "__main__":
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log.info("listening on :%s", PORT)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
