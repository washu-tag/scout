"""Auth - two paths, first match wins.

1. **Bearer JWT** validated against Keycloak JWKS. Used by the OWUI tool
   path (forwards `__oauth_token__`) and by anything else that wants to
   present a real end-user token. Validates signature + exp + iss + aud
   (`aud=report-viewer`, stamped by the `report-viewer-audience` client
   scope).
2. **oauth2-proxy header** (`X-Auth-Request-Preferred-Username`) - the
   ingress path. Trusted only when the request also carries the
   `X-Report-Viewer-Gateway` secret that Traefik injects, so a
   pod-to-pod request (OWUI, Prometheus) can't forge the username.

Both populate the same `User(sub=...)` model. Downstream code never
needs to know which path produced the identity. The user JWT is not
forwarded to Trino. `trino_client` uses the `report_viewer_svc` service
principal and impersonates this `sub` via X-Trino-User (ADR 0022).
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from dataclasses import dataclass

from fastapi import Header, HTTPException, status
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError

from . import jwks
from .config import ALLOWED_JWT_ALGS, settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class User:
    sub: str  # owner_sub stored on the search row; also sent as X-Trino-User


def _gateway_ok(header: str | None) -> bool:
    secret = settings.gateway_secret
    if not secret or header is None:
        return False
    return hmac.compare_digest(header.encode(), secret.encode())


def _bearer_token(auth_header: str | None) -> str | None:
    if not auth_header:
        return None
    parts = auth_header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1]:
        return parts[1]
    return None


def _validate_jwt(token: str) -> str | None:
    """Validate `token` against Keycloak JWKS, return `sub` or None.

    Returns None - rather than raising - on validation failure so the
    caller can fall through to the next auth path (header, shared
    secret). Logs at INFO so failed-then-fallback succeeds quietly while
    repeated failures are still searchable in Loki.
    """
    try:
        unverified = jwt.get_unverified_header(token)
    except JWTError:
        log.info("bearer rejected: malformed header")
        return None
    kid = unverified.get("kid")
    if not kid:
        log.info("bearer rejected: no kid in header")
        return None
    cache = jwks.get_default(settings.oidc_jwks_url)
    key = cache.get_key(kid)
    if key is None:
        log.info("bearer rejected: kid %s not in JWKS", kid)
        return None
    key_alg = key.get("alg")
    if key_alg and key_alg not in ALLOWED_JWT_ALGS:
        log.info("bearer rejected: JWK alg %s not in allowlist", key_alg)
        return None
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=list(ALLOWED_JWT_ALGS),
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
        )
    except ExpiredSignatureError:
        log.info("bearer rejected: token expired")
        return None
    except JWTClaimsError as exc:
        log.info("bearer rejected: claim mismatch (%s)", exc)
        return None
    except JWTError as exc:
        log.info("bearer rejected: signature/decode (%s)", exc)
        return None
    # python-jose 3.5 accepts tokens with no `aud` even when `audience=` is passed.
    aud = claims.get("aud")
    aud_list = [aud] if isinstance(aud, str) else (aud or [])
    if settings.oidc_audience not in aud_list:
        log.info("bearer rejected: aud missing/mismatch")
        return None
    # Prefer preferred_username because Trino is configured with
    # http-server.authentication.jwt.principal-field=preferred_username
    # (matches Jupyter/Voila). Using sub (UUID) here would cause Trino to
    # reject the request as "principal X cannot impersonate UUID Y" when
    # the JWT-derived principal != X-Trino-User header.
    sub = claims.get("preferred_username") or claims.get("sub")
    if not sub:
        log.info("bearer rejected: no preferred_username/sub")
        return None
    return sub


async def get_current_user(
    authorization: str | None = Header(default=None),
    x_auth_request_preferred_username: str | None = Header(default=None),
    x_report_viewer_gateway: str | None = Header(default=None),
) -> User:
    # Path 1: Bearer JWT (highest trust; carries the real user identity).
    token = _bearer_token(authorization)
    if token:
        # JWKS fetch on cache miss is blocking; keep it off the event loop.
        sub = await asyncio.to_thread(_validate_jwt, token)
        if sub:
            return User(sub=sub)
        # Bearer was present but invalid - 401 directly instead of falling
        # through. If a caller bothered to send a bearer, they meant to
        # authenticate as that user; silently downgrading to header trust
        # would mask token-expiry bugs.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="bearer token validation failed",
        )

    # Path 2: oauth2-proxy header, trusted only with Traefik's shared secret.
    if x_auth_request_preferred_username and _gateway_ok(x_report_viewer_gateway):
        return User(sub=x_auth_request_preferred_username)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication required",
    )
