"""The fragment contract. Public, versioned, and deliberately narrow.

Apps write against this file. It is the whole vocabulary available for
describing a Keycloak client: a fragment says what the app is and what its users
need, and Scout decides which Keycloak objects that becomes. There is no syntax
for protocol mappers, scope lists, `fullScopeAllowed`, service accounts, PKCE, or
the login flow -- those live in `translate`. A field a fragment cannot vary is a
field it does not have; the browser code flow with PKCE `S256` is the only shape
Scout issues, so there is nothing to say about either.

Unknown fields are rejected rather than ignored, unlike launchpad chips (ADR
0034): a chip that drops a key renders a slightly wrong tile, but a fragment
that drops `publicClient: true` leaves the author believing they shipped a
public client. `apiVersion` carries forward compatibility instead.
"""

from __future__ import annotations

import urllib.parse
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from . import yamlio

API_VERSION = "keycloak.scout.xnat.org/v1alpha1"
KIND = "KeycloakFragment"

# Conservative on purpose: these reach admin-API URL paths and token claims.
NAME_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$"

Name = Annotated[str, Field(pattern=NAME_PATTERN)]

# Claim names a fragment may not write into: either the token's own structure
# or something Scout's authorization reads. `groups` is the default.
RESERVED_CLAIMS = frozenset(
    {
        "acr",
        "aud",
        "azp",
        "client_id",
        "email",
        "email_verified",
        "exp",
        "iat",
        "iss",
        "jti",
        "name",
        "nbf",
        "nonce",
        "preferred_username",
        "realm_access",
        "resource_access",
        "scope",
        "session_state",
        "sid",
        "sub",
        "typ",
    }
)


class FragmentError(ValueError):
    """A fragment that will not be applied, with a message an author can act on."""


class SecretRef(BaseModel):
    """A pointer to a Secret in the fragment's own namespace, never a secret.

    Read by name under a `resourceNames`-scoped grant the app's own chart must
    supply; without it the fragment is rejected for an unreadable secretRef.
    """

    model_config = ConfigDict(extra="forbid")

    name: Name
    # A detail of the app's own chart rather than of the contract.
    key: str = "client-secret"


class ClientSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: Name = Field(alias="clientId")
    display_name: str = Field(alias="displayName", max_length=255)
    description: str = Field("", max_length=512)
    app_url: str = Field(alias="appUrl")
    redirect_uris: list[str] = Field(alias="redirectUris", min_length=1)
    # Patterned for the same reason as clientId: a role name is interpolated
    # into an admin-API path, where `../` would walk out of the client.
    roles: list[Name] = Field(default_factory=list)
    role_claim: Name = Field("groups", alias="roleClaim")
    secret_ref: SecretRef = Field(alias="secretRef")
    # tier role -> the fragment's own roles to compose into it.
    grants: dict[str, list[str]] = Field(default_factory=dict)

    @property
    def app_origin(self) -> str:
        """`appUrl` reduced to a web origin: scheme://host[:port].

        `webOrigins` is compared against a browser's `Origin` header, which is
        an origin and never carries a path, so a path-bearing or even just
        slash-terminated `appUrl` used verbatim could only ever fail to match --
        and Keycloak validates redirect URIs but not web origins, so it would
        accept the value and silently never match it. The base realm's own
        clients carry the bare site origin for the same reason, including the
        ones served under a path.

        Falls back to the raw value when there is no origin to build from,
        which `check_site_rules` rejects moments later; deciding that here
        would mean raising from a property.
        """
        return _origin(self.app_url) or self.app_url

    @model_validator(mode="after")
    def _check_role_claim(self) -> ClientSpec:
        root = self.role_claim.split(".", 1)[0]
        if root in RESERVED_CLAIMS:
            raise ValueError(
                f"roleClaim {self.role_claim!r} writes into {root!r}, a reserved "
                "claim; a fragment may not overwrite it "
                "(omit roleClaim for 'groups')"
            )
        return self

    @model_validator(mode="after")
    def _check_roles_unique(self) -> ClientSpec:
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("roles contains duplicates")
        return self

    @model_validator(mode="after")
    def _check_redirect_uris_unique(self) -> ClientSpec:
        """Keycloak stores these in a Set, so a duplicate reads back
        deduplicated and the client is rewritten as drifted every pass."""
        if len(set(self.redirect_uris)) != len(self.redirect_uris):
            raise ValueError("redirectUris contains duplicates")
        return self

    @model_validator(mode="after")
    def _check_grant_sources(self) -> ClientSpec:
        """A fragment may only grant roles it declares itself.

        Otherwise one app writes `grants: {scout-user: [other-app-admin]}` and
        hands another app's role to every Scout user.
        """
        declared = set(self.roles)
        for tier, names in self.grants.items():
            unknown = [name for name in names if name not in declared]
            if unknown:
                raise ValueError(
                    f"grants.{tier} names {', '.join(sorted(unknown))}, which "
                    "this fragment does not declare under roles"
                )
        return self


class Fragment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: str = Field(alias="apiVersion")
    kind: str
    clients: list[ClientSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_client_ids_unique(self) -> Fragment:
        ids = [client.client_id for client in self.clients]
        if len(set(ids)) != len(ids):
            raise ValueError("two clients in this fragment share a clientId")
        return self


def _origin(raw: str) -> str | None:
    """`raw` reduced to `scheme://host[:port]`, or None when it has no origin.

    The scheme's default port is left out, because a browser's `Origin` header
    omits it and an origin is matched against that header as a string.
    """
    parsed = urllib.parse.urlparse(raw)
    try:
        port = parsed.port
    except ValueError:
        return None
    if not parsed.hostname:
        return None
    if port == {"http": 80, "https": 443}.get(parsed.scheme):
        port = None
    host = f"{parsed.hostname}:{port}" if port else parsed.hostname
    return f"{parsed.scheme}://{host}"


def _https_host(raw: str, field: str) -> str:
    """The host of an https URL, or a FragmentError naming what was wrong."""
    try:
        parsed = urllib.parse.urlparse(raw)
    except ValueError as exc:
        raise FragmentError(f"{field} {raw!r} is not a URL: {exc}") from None
    if parsed.scheme != "https":
        raise FragmentError(f"{field} {raw!r} must use https")
    if "*" in raw:
        raise FragmentError(
            f"{field} {raw!r} must not contain a wildcard; "
            "name each redirect URI in full"
        )
    if parsed.username or parsed.password:
        raise FragmentError(f"{field} {raw!r} must not carry credentials")
    if parsed.fragment:
        raise FragmentError(f"{field} {raw!r} must not carry a URL fragment")
    if not parsed.hostname:
        raise FragmentError(f"{field} {raw!r} has no host")
    if "\\" in parsed.hostname:
        raise FragmentError(
            f"{field} {raw!r} must not contain a backslash; a browser reads one "
            "as a path separator, so the host is not what this reads as"
        )
    if _origin(raw) is None:
        raise FragmentError(f"{field} {raw!r} has an invalid port")
    return parsed.hostname


def check_site_rules(client: ClientSpec, *, hostname: str, tiers: list[str]) -> None:
    """The checks that need site configuration, so cannot live on the model."""
    for field, raw in [("appUrl", client.app_url)] + [
        ("redirectUris", uri) for uri in client.redirect_uris
    ]:
        host = _https_host(raw, field).removesuffix(".")
        if not hostname or (host != hostname and not host.endswith(f".{hostname}")):
            raise FragmentError(
                f"{field} {raw!r} points at {host}, which is outside the site's "
                f"own domain ({hostname})"
            )
    # Only appUrl: a redirect URI legitimately carries one, but appUrl is a base
    # URL that becomes a web origin and a post-logout redirect, and a query
    # string is meaningless in both.
    if urllib.parse.urlparse(client.app_url).query:
        raise FragmentError(f"appUrl {client.app_url!r} must not carry a query string")
    for tier in client.grants:
        if tier not in tiers:
            raise FragmentError(
                f"grants names {tier!r}, which is not a Scout tier role "
                f"({', '.join(tiers)})"
            )


def parse(text: str) -> Fragment:
    """One fragment document, or a FragmentError an author can act on."""
    try:
        raw = yamlio.safe_load(text)
    # Broad because PyYAML cannot bound nesting depth, so a deeply nested
    # document arrives as RecursionError: https://github.com/yaml/pyyaml/issues/895
    except Exception as exc:
        raise FragmentError(f"not valid YAML: {exc}") from None
    if not isinstance(raw, dict):
        raise FragmentError("a fragment must be a YAML mapping")

    # Before the body: telling an author their `roles` field is wrong is
    # misleading when the real answer is an unrecognised apiVersion.
    api_version = raw.get("apiVersion")
    if api_version != API_VERSION:
        raise FragmentError(
            f"apiVersion {api_version!r} is not supported by this reconciler "
            f"(expected {API_VERSION}); skipping the whole document"
        )
    if raw.get("kind") != KIND:
        raise FragmentError(f"kind {raw.get('kind')!r} is not {KIND}")

    try:
        return Fragment.model_validate(raw)
    except ValidationError as exc:
        raise FragmentError("; ".join(_problems(exc))) from None


def _problems(exc: ValidationError) -> list[str]:
    out = []
    for error in exc.errors():
        where = ".".join(str(part) for part in error["loc"]) or "(document)"
        out.append(f"{where}: {error['msg']}")
    return out
