"""Token-provider implementations.

A token provider is any zero-arg callable returning a Keycloak access
token string. Callers that already have a token in hand (e.g. the
frontend service reading a request header) can pass a plain ``lambda``;
the Hub-API provider below is for the JupyterHub default path.
"""

from __future__ import annotations

import os
from typing import Callable

import requests

from .errors import ScoutXnatAuthError

TokenProvider = Callable[[], str]


class JupyterHubTokenProvider:
    """Fetch the user's Keycloak access token from JupyterHub's auth_state.

    Reads ``JUPYTERHUB_API_URL``, ``JUPYTERHUB_API_TOKEN``, and
    ``JUPYTERHUB_USER`` from the environment (JupyterHub injects these
    into every spawned notebook pod), then calls
    ``GET {api_url}/users/{user}`` with the API token. The Scout deploy
    grants the spawned server's role the ``admin:auth_state!user`` scope
    so ``auth_state.access_token`` is included in the response.

    On freshness: this GET goes through the Hub's ``get_current_user``,
    which refreshes the requesting principal's auth (via the
    authenticator's ``refresh_user``) when it is older than
    ``auth_refresh_age`` — but only while the Keycloak *refresh token* is
    still valid. If the notebook has been idle past the Keycloak SSO
    session lifetime, the refresh token is gone, the Hub answers 401, and
    there is no token to hand back: the user must re-login to JupyterHub.
    So most of the time the token we return is fresh, but an expired SSO
    session surfaces as a clear ``ScoutXnatAuthError`` (see ``__call__``).
    """

    def __init__(self, timeout: float = 10.0) -> None:
        self._api_url = os.environ.get("JUPYTERHUB_API_URL")
        self._api_token = os.environ.get("JUPYTERHUB_API_TOKEN")
        self._user = os.environ.get("JUPYTERHUB_USER")
        self._timeout = timeout
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
                "JupyterHubTokenProvider requires these env vars (set "
                f"automatically inside Scout notebooks): {', '.join(missing)}"
            )

    def __call__(self) -> str:
        url = f"{self._api_url.rstrip('/')}/users/{self._user}"
        try:
            resp = requests.get(
                url,
                headers={"Authorization": f"token {self._api_token}"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise ScoutXnatAuthError(f"JupyterHub API unreachable: {exc}") from exc
        if resp.status_code == 401:
            # The Hub could not authenticate/refresh us — almost always because
            # the Keycloak SSO session (refresh token) has expired after a long
            # idle. There is no token to recover; the user must re-login.
            raise ScoutXnatAuthError(
                "JupyterHub rejected the API request (401). Your login session "
                "has most likely expired — re-login to JupyterHub (and restart "
                "your server if prompted), then retry."
            )
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
