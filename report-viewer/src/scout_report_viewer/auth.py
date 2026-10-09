"""Auth - a single inbound path: Bearer JWT, validated against Keycloak JWKS.

Used by the OWUI tool path (forwards `__oauth_token__`), by the SPA's own
browser-side requests (Traefik's report-viewer-scoped forwardAuth middleware
injects oauth2-proxy's ID token as `Authorization: Bearer <id_token>` on every
request through the ingress - the browser itself never sets this header, see
`ansible/roles/oauth2-proxy/tasks/deploy.yaml`), and by anything else that
wants to present a real end-user token. Validates signature + exp + iss + aud
(`aud=report-viewer`, stamped by the `report-viewer-audience` client scope).

Behind a proxy that authenticates the browser itself and forwards the user's
access token in a header (AWS ALB OIDC), setting `forwarded_token_header`
validates that token the same way.

`User.roles` comes from `resource_access.<oidc_roles_client_id>.roles` -
Keycloak's standard client-role claim shape, delivered by a
`report-viewer-roles-mapper` on the `report-viewer-audience` client scope
(739-bearer-token-spike; see `helm/keycloak-config-cli/files/scout-realm.json`).
A caller whose token carries no such claim just gets an empty set, not a
validation failure - visibility gating degrades to "nothing gated is visible,"
not to a 401.

There used to be a second path here (oauth2-proxy's forwarded headers, trusted
via a `X-Report-Viewer-Gateway` shared secret) for the SPA's own requests,
back when the SPA had no way to carry a real Bearer JWT. It's retired now that
Path 1 reaches 100% of inbound traffic, including the SPA's.

The user JWT is not forwarded to Trino. `trino_client` uses the
`report_viewer_svc` service principal and impersonates this `sub` via
X-Trino-User (ADR 0022).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request, status
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError

from . import jwks
from .config import ALLOWED_JWT_ALGS, settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class User:
    sub: str  # owner_sub stored on the search row; also sent as X-Trino-User
    # Keycloak client roles (resource_access.report-viewer.roles).
    roles: frozenset[str] = frozenset()


def _bearer_token(auth_header: str | None) -> str | None:
    if not auth_header:
        return None
    parts = auth_header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1]:
        return parts[1]
    return None


def _parse_resource_access_roles(claims: dict) -> frozenset[str]:
    resource_access = claims.get("resource_access") or {}
    client_claims = resource_access.get(settings.oidc_roles_client_id) or {}
    return frozenset(client_claims.get("roles") or [])


def _validate_jwt(token: str) -> User | None:
    """Validate `token` against Keycloak JWKS, return a `User` or None.

    Returns None - rather than raising - on validation failure so the caller
    can turn it into a 401 without a traceback. Logs at INFO so this stays
    searchable in Loki without being noisy at WARNING/ERROR.
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
            # ID tokens issued alongside an access token (oauth2-proxy's normal
            # code flow) carry at_hash, binding them to that specific access
            # token. We only ever see the bearer, never its paired access
            # token, so there's nothing to compare against - and nothing to
            # gain from it anyway, since we already verify signature/exp/iss/aud.
            options={"verify_at_hash": False},
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
    return User(sub=sub, roles=_parse_resource_access_roles(claims))


async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> User:
    token = _bearer_token(authorization)
    if not token and settings.forwarded_token_header:
        token = request.headers.get(settings.forwarded_token_header)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    # JWKS fetch on cache miss is blocking; keep it off the event loop.
    user = await asyncio.to_thread(_validate_jwt, token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="bearer token validation failed",
        )
    return user
