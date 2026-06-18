"""Auth — three paths, first match wins.

1. **Bearer JWT** validated against Keycloak JWKS. Used by the OWUI tool
   path (forwards `__oauth_token__`) and by anything else that wants to
   present a real end-user token. Validates signature + exp + iss; aud
   verification is OFF by default since Scout doesn't have a dedicated
   `report-viewer-service` Keycloak client yet — flip `aud_verify=True` in
   config when one exists.
2. **oauth2-proxy header** (`X-Auth-Request-Preferred-Username`) — the
   ingress path. The NetworkPolicy restricts ingress to Traefik so the
   header can't be forged from inside the cluster.
3. **Dev shared secret** (`X-Report-Viewer-Shared-Secret` + `X-Report-Viewer-Test-User`)
   — for in-cluster smoke testing only; gated on the env-supplied
   `dev_shared_secret`.

All three populate the same `User(sub=...)` model. Downstream code never
needs to know which path produced the identity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Header, HTTPException, status
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError

from . import jwks
from .config import settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class User:
    sub: str  # owner_sub stored on the search row
    # Raw Keycloak access token, present when auth Path 1 (Bearer JWT)
    # was used. Forwarded onward to Trino so OPA evaluates row filters
    # against the real requester (ADR 0022). None for header/shared-
    # secret paths — those callers can't reach Trino.
    token: str | None = None


def _bearer_token(auth_header: str | None) -> str | None:
    if not auth_header:
        return None
    parts = auth_header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1]:
        return parts[1]
    return None


def _validate_jwt(token: str) -> str | None:
    """Validate `token` against Keycloak JWKS, return `sub` or None.

    Returns None — rather than raising — on validation failure so the
    caller can fall through to the next auth path (header, shared
    secret). Logs at INFO so failed-then-fallback succeeds quietly while
    repeated failures are still searchable in Loki.
    """
    if not settings.keycloak_jwks_url:
        # Not configured (test / local). Skip silently so the other
        # auth paths still work.
        return None
    try:
        unverified = jwt.get_unverified_header(token)
    except JWTError:
        log.info("bearer rejected: malformed header")
        return None
    kid = unverified.get("kid")
    if not kid:
        log.info("bearer rejected: no kid in header")
        return None
    cache = jwks.get_default(settings.keycloak_jwks_url)
    key = cache.get_key(kid)
    if key is None:
        log.info("bearer rejected: kid %s not in JWKS", kid)
        return None
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[key.get("alg", "RS256")],
            # Skip aud verification until a dedicated client exists; iss
            # gives us the strong signal "this token came from our IdP".
            options={"verify_aud": False},
            issuer=settings.keycloak_issuer or None,
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
    x_auth_request_access_token: str | None = Header(default=None),
    x_report_viewer_test_user: str | None = Header(default=None),
    x_report_viewer_shared_secret: str | None = Header(default=None),
) -> User:
    # Path 1: Bearer JWT (highest trust; carries the real user identity).
    token = _bearer_token(authorization)
    if token:
        sub = _validate_jwt(token)
        if sub:
            return User(sub=sub, token=token)
        # Bearer was present but invalid — 401 directly instead of falling
        # through. If a caller bothered to send a bearer, they meant to
        # authenticate as that user; silently downgrading to header trust
        # would mask token-expiry bugs.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="bearer token validation failed",
        )

    # Path 2: oauth2-proxy headers (Traefik-gated; NetworkPolicy prevents
    # in-cluster forgery). The access token is the user's Keycloak JWT
    # (oauth2-proxy `pass_access_token = true` + middleware
    # `authResponseHeaders: X-Auth-Request-Access-Token`); forwarded to
    # Trino as Bearer so OPA evaluates per requester.
    if x_auth_request_preferred_username:
        return User(
            sub=x_auth_request_preferred_username,
            token=x_auth_request_access_token,
        )

    # Path 3: dev shared secret (env-gated; default disabled).
    if (
        settings.dev_shared_secret
        and x_report_viewer_shared_secret == settings.dev_shared_secret
        and x_report_viewer_test_user
    ):
        return User(sub=x_report_viewer_test_user)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication required",
    )
