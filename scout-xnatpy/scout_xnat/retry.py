"""401-retry hook installed on xnatpy's underlying ``requests.Session``.

When XNAT returns 401 (most often: the JSESSIONID cookie has expired
past ``siteConfig.sessionTimeout``), the hook fetches a fresh access
token from the configured ``TokenProvider``, re-mints a JSESSIONID
against XNAT, swaps the cookie on the session, and re-sends the
original request once. A thread-local flag prevents the retry's own
response from re-entering the hook.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests

from .errors import ScoutXnatAuthError

_log = logging.getLogger(__name__)
_retry_state = threading.local()


def _xnatpy_cookie_domain(server: str) -> str:
    """Domain xnatpy scopes its JSESSIONID cookie to (mirrors ``xnat.connect``).

    xnatpy stores the cookie with an explicit domain derived from the server
    netloc — port stripped, ``.local`` appended to a dotless host. The refresh
    hook must re-set the cookie under the *same* (domain, path, name) key, or
    ``RequestsCookieJar`` keeps both and emits ``JSESSIONID=stale;
    JSESSIONID=fresh``; XNAT honors the first (stale) one and the retry never
    recovers the session.
    """
    domain = urlparse(server).netloc
    if ":" in domain:
        domain = domain.split(":")[0]
    if "." not in domain:
        domain = f"{domain}.local"
    return domain


def install_refresh_hook(
    connection: Any,
    server: str,
    token_provider: Callable[[], str],
    mint_jsessionid: Callable[[str, str, Any], str],
    verify: Any = True,
) -> None:
    interface = connection.interface
    cookie_domain = _xnatpy_cookie_domain(server)

    def on_response(
        response: requests.Response, **_: Any
    ) -> Optional[requests.Response]:
        if response.status_code != 401:
            return None
        if getattr(_retry_state, "active", False):
            return None
        _log.info("XNAT 401; re-fetching access token and re-minting JSESSIONID")
        try:
            new_token = token_provider()
            new_jsession = mint_jsessionid(server, new_token, verify)
        except ScoutXnatAuthError as exc:
            _log.warning("scout_xnat re-auth failed: %s", exc)
            return None
        # Set under xnatpy's exact (domain, path, name) key so this replaces
        # the stale cookie in place rather than adding a second JSESSIONID.
        interface.cookies.set(
            "JSESSIONID", new_jsession, domain=cookie_domain, path="/"
        )
        retried = response.request.copy()
        retried.headers.pop("Cookie", None)
        retried.prepare_cookies(interface.cookies)
        _retry_state.active = True
        try:
            return interface.send(retried, verify=verify, allow_redirects=False)
        finally:
            _retry_state.active = False

    hooks = interface.hooks.setdefault("response", [])
    if not isinstance(hooks, list):
        interface.hooks["response"] = [hooks, on_response]
    else:
        hooks.append(on_response)
