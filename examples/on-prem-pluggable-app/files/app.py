"""The whole service: one page, standard library only, no dependencies.

Replace this with your real app. The chart around it is the part worth copying.
"""

import html
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", "8080"))
CLIENT_ID = os.environ.get("KEYCLOAK_CLIENT_ID", "(unset)")
SECRET_FILE = Path(os.environ.get("KEYCLOAK_CLIENT_SECRET_FILE", ""))

# oauth2-proxy sets this at the ingress (ADR 0022). It is worth trusting only
# because the edge gate is the sole route to this pod.
USER_HEADER = "X-Auth-Request-Preferred-Username"

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
  <dt>Keycloak client</dt>
  <dd><code>{client}</code>, created from the fragment in this chart</dd>
  <dt>Client secret</dt>
  <dd>{secret}</dd>
</dl>
"""


def secret_status() -> str:
    """Report whether the credential the fragment points at actually landed."""
    try:
        mounted = bool(SECRET_FILE.read_text(encoding="utf-8").strip())
    except OSError:
        return f"not readable at <code>{html.escape(str(SECRET_FILE))}</code>"
    if not mounted:
        return "mounted but empty"
    return f"mounted at <code>{html.escape(str(SECRET_FILE))}</code>"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            body, content_type = b"ok", "text/plain; charset=utf-8"
        else:
            body = PAGE.format(
                user=html.escape(self.headers.get(USER_HEADER, "(header absent)")),
                header=USER_HEADER,
                client=html.escape(CLIENT_ID),
                secret=secret_status(),
            ).encode()
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
