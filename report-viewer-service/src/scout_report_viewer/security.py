"""Minimal security-headers middleware.

Replaces Traefik's shared `security-headers` middleware for this service.
The shared one sets `frame-ancestors 'none'`, which would block the chat
iframe from embedding the viewer — so we run our own with a narrower
policy here.

Order matters: install before the routes so headers apply to every
response (including streaming /export.csv).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from .config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._csp = self._build_csp()

    @staticmethod
    def _build_csp() -> str:
        # Whatever the iframe needs to actually load: Tabulator JS+CSS
        # from jsdelivr (script-src/style-src), inline JS for the small
        # bootstrap in /view (unsafe-inline), and frame-ancestors set to
        # the chat origin so the iframe embeds at all.
        ancestors = settings.csp_frame_ancestors.strip() or "'none'"
        return (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            f"frame-ancestors {ancestors};"
        )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", self._csp)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response
