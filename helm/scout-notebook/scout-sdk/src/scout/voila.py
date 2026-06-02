"""Voila integration: thread the OIDC user's access token from the
oauth2-proxy-forwarded request header into each spawned kernel.

Wiring:
  oauth2-proxy (pass_access_token=true + set_xauthrequest=true) sets
    X-Auth-Request-Access-Token on its /oauth2/auth response
  Traefik's oauth2-proxy-auth middleware (authResponseHeaders) forwards
    the header onto Voila's upstream request
  TornadoVoilaHandler.get (monkey-patched here) stashes the token in a
    contextvar for the duration of the request's async context
  ScoutMappingKernelManager.start_kernel reads the contextvar and adds
    X_AUTH_REQUEST_ACCESS_TOKEN to the spawned kernel's env
  scout.trino (in the kernel) decodes preferred_username from that env
    var and sets X-Trino-User on every Trino call

Side-effect import: `import scout.voila` applies the monkey-patch.
The handler is wrapped because Voila doesn't expose a
voila_handler_class Traitlet; the kernel manager IS configurable via
c.ServerApp.kernel_manager_class.

contextvars propagate across `await` within the same async task, so
the value set in the handler is visible to the kernel manager called
in the same request flow.
"""

import contextvars
import logging

from jupyter_server.services.kernels.kernelmanager import AsyncMappingKernelManager
from voila.tornado.handler import TornadoVoilaHandler

logger = logging.getLogger(__name__)

_access_token: contextvars.ContextVar[str] = contextvars.ContextVar(
    "scout_voila_x_auth_request_access_token", default=""
)

_original_voila_get = TornadoVoilaHandler.get


async def _scout_voila_get(self, path=None):
    token = self.request.headers.get("X-Auth-Request-Access-Token", "")
    if not token:
        logger.warning(
            "scout.voila: no X-Auth-Request-Access-Token on request; "
            "Trino queries will run as anonymous and clamp to zero rows. "
            "Verify oauth2-proxy pass_access_token=true is configured."
        )
    _access_token.set(token)
    return await _original_voila_get(self, path)


TornadoVoilaHandler.get = _scout_voila_get


class ScoutMappingKernelManager(AsyncMappingKernelManager):
    """KernelManager subclass that injects the captured access token
    into each spawned kernel's environment.

    Registered via c.ServerApp.kernel_manager_class in Voila's
    jupyter_server_config.py."""

    async def start_kernel(self, **kwargs):
        token = _access_token.get()
        env = dict(kwargs.pop("env", None) or {})
        if token:
            env["X_AUTH_REQUEST_ACCESS_TOKEN"] = token
        kwargs["env"] = env
        return await super().start_kernel(**kwargs)
