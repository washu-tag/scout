"""scout_xnat_auth: bearer-token XNAT auth wrapper for Scout JupyterHub notebooks.

Usage::

    import scout_xnat_auth
    connection = scout_xnat_auth.connect()
    for project in connection.projects.values():
        print(project.id, project.name)

The wrapper:

1. Reads the user's Keycloak access token out of JupyterHub's stored
   ``auth_state`` by calling the Hub REST API at
   ``GET {JUPYTERHUB_API_URL}/users/{JUPYTERHUB_USER}`` with the
   ``JUPYTERHUB_API_TOKEN`` env var that JupyterHub injects into every
   spawned notebook pod. The Scout deploy grants the spawned server's
   role the ``admin:auth_state!user`` scope so the response includes
   ``auth_state.access_token``; oauthenticator's ``refresh_user`` is
   triggered automatically when the cached token is past
   ``auth_refresh_age`` (default 5 min), so what we get back is fresh.
2. Mints a Scout-issued JSESSIONID by hitting XNAT once with that
   bearer token. XNAT's ``scout-auth-plugin`` BearerTokenFilter does
   the JWT validation + token exchange + user provisioning, then sets
   a JSESSIONID on the response.
3. Hands the JSESSIONID to xnatpy via ``xnat.connect(jsession=...)``,
   which gives the user the full xnatpy ORM (``connection.projects``,
   ``.subjects``, etc.) and a keepalive thread that pings XNAT every
   ~7 min so the session doesn't time out under a long-running script.
4. Installs a ``response`` hook on the underlying ``requests.Session``
   that catches 401s, re-fetches the access token (which transparently
   triggers refresh_user on the Hub side if needed), re-mints the
   JSESSIONID, and re-sends the original request once. So a notebook
   that's been idle past XNAT's session timeout recovers without the
   user having to reconnect.

Designed for the Phase C POC. Not packaged, not auto-deployed; drop
into the notebook workspace by hand.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

import requests
import xnat

_log = logging.getLogger(__name__)
_DEFAULT_SERVER = "http://xnat.xnat.svc.cluster.local"


class ScoutXnatAuthError(RuntimeError):
    """Raised when the bearer-token bootstrap fails (env vars missing,
    Hub API rejected, XNAT rejected the token, etc.)."""


class _TokenSource:
    """Fetches the user's Keycloak access token from JupyterHub's auth_state."""

    def __init__(self) -> None:
        self._api_url = os.environ.get("JUPYTERHUB_API_URL")
        self._api_token = os.environ.get("JUPYTERHUB_API_TOKEN")
        self._user = os.environ.get("JUPYTERHUB_USER")
        missing = [
            name
            for name, val in (
                ("JUPYTERHUB_API_URL", self._api_url),
                ("JUPYTERHUB_API_TOKEN", self._api_token),
                ("JUPYTERHUB_USER", self._user),
            )
            if not val
        ]
        if missing:
            raise ScoutXnatAuthError(
                "scout_xnat_auth.connect() requires these env vars (set "
                f"automatically inside Scout notebooks): {', '.join(missing)}"
            )

    def fetch(self) -> str:
        url = f"{self._api_url.rstrip('/')}/users/{self._user}"
        try:
            resp = requests.get(
                url,
                headers={"Authorization": f"token {self._api_token}"},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ScoutXnatAuthError(f"JupyterHub API unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise ScoutXnatAuthError(
                f"JupyterHub API returned {resp.status_code} for {url}: {resp.text[:200]}"
            )
        body = resp.json()
        auth_state = body.get("auth_state")
        if not auth_state:
            raise ScoutXnatAuthError(
                "JupyterHub returned no auth_state for this user. The spawned "
                "server's role probably lacks admin:auth_state!user — check "
                "hub.loadRoles in the deployed values."
            )
        token = auth_state.get("access_token")
        if not token:
            raise ScoutXnatAuthError(
                "auth_state has no access_token. Is GenericOAuthenticator's "
                "enable_auth_state still true?"
            )
        return token


def _mint_jsessionid(server: str, token: str, verify: Any = True) -> str:
    try:
        resp = requests.get(
            server.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
            verify=verify,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise ScoutXnatAuthError(f"XNAT unreachable at {server}: {exc}") from exc
    if resp.status_code in (401, 403):
        raise ScoutXnatAuthError(
            f"XNAT rejected the bearer token ({resp.status_code}). "
            "User probably lacks the xnat-access role; check Keycloak group "
            "membership for scout-user."
        )
    jsessionid = resp.cookies.get("JSESSIONID")
    if not jsessionid:
        raise ScoutXnatAuthError(
            f"XNAT returned {resp.status_code} but did not set a JSESSIONID. "
            "Check that scout-auth-plugin's BearerTokenFilter is loaded."
        )
    return jsessionid


_retry_state = threading.local()


def _install_refresh_hook(
    connection: Any,
    server: str,
    token_source: _TokenSource,
    verify: Any = True,
) -> None:
    """Replace stale-session 401s with a transparent re-auth + retry.

    The hook fires on every response from ``connection.interface``
    (xnatpy's underlying ``requests.Session``). On 401 it fetches a
    fresh token, mints a fresh JSESSIONID, swaps the cookie on the
    session, and re-sends the original request. The thread-local
    ``_retry_state.active`` flag prevents the retry's own response
    from triggering the hook again.
    """
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
            new_token = token_source.fetch()
            new_jsession = _mint_jsessionid(server, new_token, verify=verify)
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


def connect(server: Optional[str] = None, verify: Any = True, **xnatpy_kwargs: Any):
    """Open an xnatpy connection to XNAT using the notebook's Keycloak access token.

    Parameters
    ----------
    server : str, optional
        XNAT base URL. Defaults to the in-cluster service URL
        (``http://xnat.xnat.svc.cluster.local``), which is the only path
        Scout notebooks should use — bypasses Traefik / oauth2-proxy.
    verify : bool or str, optional
        Forwarded to ``requests`` and ``xnat.connect``. Default ``True``.
    **xnatpy_kwargs
        Any other keyword args are passed to ``xnat.connect``
        (e.g. ``no_parse_model``, ``loglevel``).

    Returns
    -------
    XNATSession
        A live xnatpy connection. Use the standard xnatpy API on it
        (``.projects``, ``.subjects``, ``.experiments``, ``.get``,
        ``.post``, etc.). On session expiry, the next call retries
        once with a freshly-minted JSESSIONID.
    """
    server = server or _DEFAULT_SERVER
    token_source = _TokenSource()
    token = token_source.fetch()
    jsessionid = _mint_jsessionid(server, token, verify=verify)
    connection = xnat.connect(
        server=server,
        jsession=jsessionid,
        verify=verify,
        **xnatpy_kwargs,
    )
    _install_refresh_hook(connection, server, token_source, verify=verify)
    return connection


__all__ = ["connect", "ScoutXnatAuthError"]
