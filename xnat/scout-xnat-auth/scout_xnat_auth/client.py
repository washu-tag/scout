"""Public ``connect()`` entrypoint and the xnatpy wrapping."""

from __future__ import annotations

from typing import Any, Optional

import requests
import xnat

from .errors import ScoutXnatAuthError
from .retry import install_refresh_hook
from .token_providers import JupyterHubTokenProvider, TokenProvider

_DEFAULT_SERVER = "http://xnat.xnat.svc.cluster.local"


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


def connect(
    server: Optional[str] = None,
    token_provider: Optional[TokenProvider] = None,
    verify: Any = True,
    **xnatpy_kwargs: Any,
):
    """Open an xnatpy connection to XNAT using a Keycloak access token.

    Parameters
    ----------
    server : str, optional
        XNAT base URL. Defaults to the in-cluster service URL
        (``http://xnat.xnat.svc.cluster.local``). The in-cluster path
        bypasses Traefik / oauth2-proxy and is the only path Scout
        callers should use.
    token_provider : Callable[[], str], optional
        Returns a fresh Keycloak access token on each invocation.
        Called once at connect time and again on every 401 from XNAT.
        If omitted, ``JupyterHubTokenProvider`` is used (the default
        path for Scout notebooks).
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
    if token_provider is None:
        token_provider = JupyterHubTokenProvider()

    token = token_provider()
    jsessionid = _mint_jsessionid(server, token, verify=verify)
    connection = xnat.connect(
        server=server,
        jsession=jsessionid,
        verify=verify,
        **xnatpy_kwargs,
    )
    install_refresh_hook(
        connection,
        server,
        token_provider,
        _mint_jsessionid,
        verify=verify,
    )
    return connection
