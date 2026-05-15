"""scout_xnat: bearer-token XNAT auth wrapper for Scout.

Two consumers today:

* The Scout JupyterHub singleuser pod, where the user's Keycloak access
  token is fetched from JupyterHub's stored ``auth_state`` via the Hub
  REST API.
* The ``scout-xnat-frontend`` FastAPI service, where the access token
  arrives in the ``X-Auth-Request-Access-Token`` request header from
  oauth2-proxy and is forwarded onto XNAT.

Both call ``connect()``; only the ``token_provider`` differs. With no
``token_provider``, the Hub-API provider is used (Jupyter default).

Usage (Jupyter)::

    import scout_xnat
    conn = scout_xnat.connect()
    for p in conn.projects.values():
        print(p.id, p.name)

Usage (service, per-request token from a header)::

    from scout_xnat import connect
    conn = connect(
        server="http://xnat.xnat.svc.cluster.local",
        token_provider=lambda: request.headers["X-Auth-Request-Access-Token"],
    )
"""

from .client import connect
from .errors import (
    ScoutXnatAuthError,
    XnatBearerRejectedError,
    XnatUnreachableError,
)
from .token_providers import JupyterHubTokenProvider, TokenProvider

__all__ = [
    "connect",
    "ScoutXnatAuthError",
    "XnatBearerRejectedError",
    "XnatUnreachableError",
    "TokenProvider",
    "JupyterHubTokenProvider",
]
