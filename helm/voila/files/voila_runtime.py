"""Voila server-side runtime: thread the OIDC user's identity from the
oauth2-proxy-forwarded request header into each spawned kernel.

Chart-shipped (not part of the scout SDK) because it's pure Voila plumbing
with no user-facing API; loaded only by Voila's jupyter_server config
(voila.py). The scout-notebook image already has Voila + jupyter-server,
which this module depends on.

Wiring:
  oauth2-proxy (set_xauthrequest=true) sets X-Auth-Request-Preferred-Username
    on its /oauth2/auth response
  Traefik's oauth2-proxy-auth middleware (authResponseHeaders) forwards
    the header onto Voila's upstream request
  TornadoVoilaHandler.get (monkey-patched here) stashes the username in a
    contextvar for the duration of the request's async context
  ScoutMappingKernelManager.start_kernel reads the contextvar and adds the
    username to the spawned kernel's env as X_AUTH_REQUEST_PREFERRED_USERNAME
  scout.connect() / scout.query() (in the kernel) read that env var and
    set X-Trino-User on every Trino call

Behind an ALB-OIDC edge (aws mode) there is no oauth2-proxy. The ALB
authenticates the browser and forwards the user's Keycloak access token in
a header instead. Setting VOILA_FORWARDED_TOKEN_HEADER switches to that
path: the token's signature (JWKS), issuer, expiry and issuing client (azp)
are validated, and its preferred_username replaces the oauth2-proxy header,
which is then ignored. Nothing else in the flow changes. The validation is
what keeps the username unforgeable: Voila's NetworkPolicy can't admit only
the ALB, whose targets are pod IPs.

Only the username crosses into the kernel — not the raw access token. The
kernel runs user-authored notebook code, so threading a live bearer token
into its environment would hand that code an exfiltratable credential; the
username is all the scout SDK needs for X-Trino-User impersonation (Trino +
OPA enforce the actual access against the impersonated user's attributes).

Side-effect import: `import voila_runtime` applies the monkey-patch. The
handler is wrapped because Voila doesn't expose a voila_handler_class
Traitlet; the kernel manager IS configurable, via Voila's
`VoilaConfiguration.multi_kernel_manager_class` (set in voila.py) — NOT
`c.ServerApp.kernel_manager_class`, which Voila does not consult.

contextvars propagate across `await` within the same async task, so the
value set in the handler is visible to the kernel manager called in the
same request flow. This also keeps concurrent users isolated: each request
runs in its own task, so one user's username can't leak into another's kernel.

The monkey-patch targets TornadoVoilaHandler (the subclass that actually
defines `get` for Voila's Tornado-mode routes), not the base VoilaHandler —
VoilaHandler defines only `get_generator`, and Tornado dispatches HTTP GETs
to the subclass's `get`, so patching the base class is a no-op.

Caveat: Voila's `preheat_kernel` (off by default) starts kernels at server
boot, outside any request context — a preheated kernel carries no username
and the SDK falls back to anonymous. This per-request capture assumes the
default lazy, per-render kernel spawn.

Kernel websocket filter: playbook pages only need widget messages on the
websocket to their kernel. ScoutKernelWebsocketConnection (registered via
c.Voila.kernel_websocket_connection_class in voila.py) forwards only the
message types the playbooks' widgets send and drops the rest.
"""

import asyncio
import contextvars
import http.client
import json
import logging
import os

import jwt
from jupyter_server.services.kernels.connection.channels import (
    ZMQChannelsWebsocketConnection,
)
from jupyter_server.services.kernels.kernelmanager import AsyncMappingKernelManager
from voila.tornado.handler import TornadoVoilaHandler

logger = logging.getLogger(__name__)

# ALB-OIDC edge (see module docstring). Unset = oauth2-proxy edge.
FORWARDED_TOKEN_HEADER = os.environ.get("VOILA_FORWARDED_TOKEN_HEADER", "")
OIDC_ISSUER = os.environ.get("VOILA_OIDC_ISSUER", "")
# The Keycloak client the edge signs users in with. Tokens issued to any other
# client are rejected, so a service holding users' tokens for its own client
# can't replay them here as those users. It does not separate apps behind the
# same edge: every app there receives tokens with this azp, and anything that
# can read their request headers could replay one within its lifetime.
OIDC_AUTHORIZED_PARTY = os.environ.get("VOILA_OIDC_AUTHORIZED_PARTY", "")
_jwks_client = None
if FORWARDED_TOKEN_HEADER:
    _jwks_url = os.environ.get("VOILA_OIDC_JWKS_URL", "")
    if not (OIDC_ISSUER and OIDC_AUTHORIZED_PARTY and _jwks_url):
        raise RuntimeError(
            "VOILA_FORWARDED_TOKEN_HEADER requires VOILA_OIDC_ISSUER, "
            "VOILA_OIDC_JWKS_URL and VOILA_OIDC_AUTHORIZED_PARTY"
        )
    # Caches the key set and refetches it when a token's kid is unknown (PyJWT
    # rate-limits those refetches). Fetches hold a lock, so a short timeout keeps
    # a hung endpoint from queueing every render behind it.
    _jwks_client = jwt.PyJWKClient(_jwks_url, lifespan=300, timeout=5)

_preferred_username: contextvars.ContextVar[str] = contextvars.ContextVar(
    "voila_runtime_x_auth_request_preferred_username", default=""
)

_original_voila_get = TornadoVoilaHandler.get


def _username_from_token(token):
    """Return the preferred_username of a valid forwarded access token, else ""."""
    try:
        # The PyJWK itself, not .key: decode then rejects a kid whose key type
        # doesn't match the token's alg instead of raising TypeError.
        key = _jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=OIDC_ISSUER,
            leeway=30,
            # The edge client's tokens carry no audience for Voila; azp below
            # pins the issuing client instead.
            options={"verify_aud": False, "require": ["exp", "iss", "azp"]},
        )
    # Non-PyJWT errors: a JWKS response that isn't JSON (ValueError), or a
    # dropped connection the PyJWT version doesn't wrap.
    except (jwt.PyJWTError, OSError, ValueError, http.client.HTTPException) as exc:
        logger.warning("voila_runtime: forwarded token rejected: %s", exc)
        return ""
    if claims["azp"] != OIDC_AUTHORIZED_PARTY:
        logger.warning("voila_runtime: forwarded token rejected: azp %r", claims["azp"])
        return ""
    # Keycloak ID tokens share the client, signature and claims; only access
    # tokens are accepted.
    if claims.get("typ") != "Bearer":
        logger.warning(
            "voila_runtime: forwarded token rejected: typ %r", claims.get("typ")
        )
        return ""
    username = claims.get("preferred_username", "")
    if not username:
        logger.warning("voila_runtime: forwarded token has no preferred_username")
    return username


async def _voila_runtime_get(self, path=None):
    if FORWARDED_TOKEN_HEADER:
        token = self.request.headers.get(FORWARDED_TOKEN_HEADER, "")
        if token:
            # A JWKS fetch on a cache miss blocks; keep it off the event loop.
            username = await asyncio.to_thread(_username_from_token, token)
        else:
            username = ""
            logger.warning(
                "voila_runtime: no %s on request; Trino queries will run as "
                "anonymous and clamp to zero rows.",
                FORWARDED_TOKEN_HEADER,
            )
    else:
        username = self.request.headers.get("X-Auth-Request-Preferred-Username", "")
        if not username:
            logger.warning(
                "voila_runtime: no X-Auth-Request-Preferred-Username on request; "
                "Trino queries will run as anonymous and clamp to zero rows. "
                "Verify oauth2-proxy set_xauthrequest=true and the forwardAuth "
                "middleware forwards X-Auth-Request-Preferred-Username."
            )
    _preferred_username.set(username)
    return await _original_voila_get(self, path)


TornadoVoilaHandler.get = _voila_runtime_get


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


# Message types the playbooks' widgets send to the kernel, pinned from observing
# all four playbooks under Voila 0.5.12, jupyter_server 2.20 and ipywidgets 8.1.
# comm_info_request wasn't observed: Voila's widget manager sends it on a
# (re)connect when the kernel takes over 4s to return widget state, e.g. while a
# callback is running. Re-check all of them after upgrading any of those.
ALLOWED_INCOMING_MSG_TYPES = frozenset(
    {"comm_close", "comm_info_request", "comm_msg", "comm_open", "kernel_info_request"}
)

V1_SUBPROTOCOL = "v1.kernel.websocket.jupyter.org"


def _incoming_msg_type(ws_msg, subprotocol):
    """Return the header msg_type of a frame from the browser, or None if unreadable.

    Reads the header from the same bytes jupyter_server forwards, so the type
    checked is the type the kernel acts on.
    """
    try:
        if subprotocol == V1_SUBPROTOCOL:
            # An 8-byte part count, then that many 8-byte offsets: channel,
            # header, parent_header, ... A count that doesn't fit the frame is
            # malformed.
            count = int.from_bytes(ws_msg[:8], "little")
            if not 3 <= count < len(ws_msg) // 8:
                return None
            start = int.from_bytes(ws_msg[16:24], "little")
            end = int.from_bytes(ws_msg[24:32], "little")
            header = json.loads(ws_msg[start:end].decode("utf-8"))
        elif isinstance(ws_msg, str):
            header = json.loads(ws_msg)["header"]
        else:
            # Legacy-protocol binary frames carry buffers. Voila's frontend
            # negotiates v1, so only a hand-built client sends these.
            return None
    except (TypeError, ValueError, KeyError, RecursionError):
        return None
    msg_type = header.get("msg_type") if isinstance(header, dict) else None
    return msg_type if isinstance(msg_type, str) else None


class ScoutKernelWebsocketConnection(ZMQChannelsWebsocketConnection):
    """Forwards only ALLOWED_INCOMING_MSG_TYPES from the browser to the kernel.

    Registered via c.Voila.kernel_websocket_connection_class in voila.py."""

    def handle_incoming_message(self, incoming_msg):
        msg_type = _incoming_msg_type(incoming_msg, self.subprotocol)
        if msg_type in ALLOWED_INCOMING_MSG_TYPES:
            super().handle_incoming_message(incoming_msg)
            return
        # The type is client-supplied: %.80r escapes and truncates it. Never log
        # the frame itself.
        logger.warning(
            "voila_runtime: dropped incoming kernel message of type %.80r on kernel %s",
            msg_type,
            self.kernel_id,
        )
