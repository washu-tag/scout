"""Voila customization: thread the OIDC user's identity from the
oauth2-proxy-forwarded request header into the spawned notebook kernel's
environment.

Wiring:
  oauth2-proxy (set_xauthrequest=true) sets X-Auth-Request-Preferred-Username
    on its /oauth2/auth response
  Traefik's oauth2-proxy-auth middleware (authResponseHeaders) forwards
    the header onto Voila's incoming request
  VoilaHandler.get (patched here) reads the header and stashes the username
    in a contextvar for the duration of the request's async context
  ScoutMappingKernelManager.start_kernel reads the contextvar and adds the
    username to the spawned kernel's env as X_AUTH_REQUEST_PREFERRED_USERNAME
  scout_trino.py inside the kernel reads that env var and sets X-Trino-User
    on every Trino call

Only the username crosses into the kernel — not the raw access token. The
kernel runs user-authored notebook code, so threading a live bearer token
into its environment would hand that code an exfiltratable credential; the
username is all scout_trino needs for X-Trino-User impersonation (Trino + OPA
enforce the actual access against the impersonated user's attributes).

The handler is wrapped via monkey-patch rather than subclass + class config
because Voila doesn't expose a `voila_handler_class` Traitlet — upstream
registers VoilaHandler directly in Voila.init_handlers(). The kernel manager
IS configurable, via Voila's `VoilaConfiguration.multi_kernel_manager_class`
(set in voila.py) — NOT `c.ServerApp.kernel_manager_class`, which Voila does
not consult.

contextvars propagate across `await` within the same async task, so the
value set in the handler is visible to the kernel manager called in the same
request flow. This also keeps concurrent users isolated: each request runs in
its own task, so one user's username can't leak into another's kernel.

The monkey-patch targets TornadoVoilaHandler (the subclass that actually
defines `get` for Voila's Tornado-mode routes), not the base VoilaHandler —
VoilaHandler defines only `get_generator`, and Tornado dispatches HTTP GETs
to the subclass's `get`, so patching the base class is a no-op.

Caveat: Voila's `preheat_kernel` (off by default) starts kernels at server
boot, outside any request context — a preheated kernel carries no username
and scout_trino falls back to anonymous. This per-request capture assumes the
default lazy, per-render kernel spawn.
"""

import contextvars
import logging

from jupyter_server.services.kernels.kernelmanager import AsyncMappingKernelManager
from voila.tornado.handler import TornadoVoilaHandler

logger = logging.getLogger(__name__)

_preferred_username: contextvars.ContextVar[str] = contextvars.ContextVar(
    "scout_x_auth_request_preferred_username", default=""
)

_original_voila_get = TornadoVoilaHandler.get


async def _scout_voila_get(self, path=None):
    username = self.request.headers.get("X-Auth-Request-Preferred-Username", "")
    if not username:
        logger.warning(
            "scout_voila: no X-Auth-Request-Preferred-Username on request; "
            "Trino queries will run as anonymous and clamp to zero rows. "
            "Verify oauth2-proxy set_xauthrequest=true and the forwardAuth "
            "middleware forwards X-Auth-Request-Preferred-Username."
        )
    _preferred_username.set(username)
    return await _original_voila_get(self, path)


TornadoVoilaHandler.get = _scout_voila_get


class ScoutMappingKernelManager(AsyncMappingKernelManager):
    """KernelManager subclass that injects the captured username into the
    spawned kernel's environment.

    Registered via c.VoilaConfiguration.multi_kernel_manager_class in
    voila.py."""

    async def start_kernel(self, **kwargs):
        username = _preferred_username.get()
        env = dict(kwargs.pop("env", None) or {})
        if username:
            env["X_AUTH_REQUEST_PREFERRED_USERNAME"] = username
        kwargs["env"] = env
        return await super().start_kernel(**kwargs)
