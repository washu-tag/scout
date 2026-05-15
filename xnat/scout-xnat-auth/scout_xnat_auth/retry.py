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

import requests

from .errors import ScoutXnatAuthError

_log = logging.getLogger(__name__)
_retry_state = threading.local()


def install_refresh_hook(
    connection: Any,
    server: str,
    token_provider: Callable[[], str],
    mint_jsessionid: Callable[[str, str, Any], str],
    verify: Any = True,
) -> None:
    interface = connection.interface

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
            _log.warning("scout_xnat_auth re-auth failed: %s", exc)
            return None
        interface.cookies.set("JSESSIONID", new_jsession)
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
