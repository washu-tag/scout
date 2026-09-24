"""Fragment to Keycloak representation. Pure, no I/O.

Everything Scout decides rather than the app is here: the flow set,
`fullScopeAllowed: false`, the role-claim mapper, PKCE, the client-scope sets,
and the ownership stamp. The scope sets match the base realm's own clients, so a
fragment-produced client and a hand-written one differ only in provenance and in
PKCE, which a fragment's client always enforces and most base-realm clients
predate.
"""

from __future__ import annotations

from .fragment import ClientSpec

# A client without this attribute is not ours and is never touched.
STAMP_ATTRIBUTE = "scout.fragment.reconciler"
# Which ConfigMap produced the client. Provenance only -- not part of the
# ownership test, so a fragment moving to a new ConfigMap is an update rather
# than a delete-and-recreate with a window where the client is absent.
SOURCE_ATTRIBUTE = "scout.fragment.source"

PROTOCOL = "openid-connect"

# A fragment has no syntax for changing these.
DEFAULT_CLIENT_SCOPES = ["web-origins", "acr", "profile", "roles", "basic", "email"]
OPTIONAL_CLIENT_SCOPES = ["address", "phone", "organization", "offline_access"]

ROLE_MAPPER_NAME = "client-roles"


def stamp(source: str) -> dict[str, str]:
    return {STAMP_ATTRIBUTE: "true", SOURCE_ATTRIBUTE: source}


def is_ours(client: dict) -> bool:
    """The only question that gates a write to an existing client."""
    return (client.get("attributes") or {}).get(STAMP_ATTRIBUTE) == "true"


def source_of(client: dict) -> str:
    return (client.get("attributes") or {}).get(SOURCE_ATTRIBUTE, "")


def client_representation(spec: ClientSpec, *, secret: str, source: str) -> dict:
    """The ClientRepresentation to POST, or to compare a live client against.

    `fullScopeAllowed: false` is set at create time and never patched in
    afterwards: the other order leaves a window where the client carries every
    realm role in its token.
    """
    attributes = dict(stamp(source))
    attributes["post.logout.redirect.uris"] = spec.app_url
    attributes["pkce.code.challenge.method"] = "S256"

    return {
        "clientId": spec.client_id,
        "name": spec.display_name,
        "description": spec.description,
        "enabled": True,
        "protocol": PROTOCOL,
        "publicClient": False,
        "bearerOnly": False,
        "clientAuthenticatorType": "client-secret",
        "secret": secret,
        # A fragment has no syntax for consent, and an app's own login prompt is
        # not where Scout asks for it.
        "consentRequired": False,
        "fullScopeAllowed": False,
        # The browser code flow, on for every fragment. Set explicitly rather
        # than left to Keycloak's default so that a flow turned off in the admin
        # console reads as drift.
        "standardFlowEnabled": True,
        # Not expressible in a fragment, and all off. Each is a capability an
        # app would otherwise be granting itself.
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "redirectUris": list(spec.redirect_uris),
        # The origin, not the URL: see `ClientSpec.app_origin`.
        "webOrigins": [spec.app_origin],
        "attributes": attributes,
        "defaultClientScopes": list(DEFAULT_CLIENT_SCOPES),
        "optionalClientScopes": list(OPTIONAL_CLIENT_SCOPES),
    }


def role_representations(spec: ClientSpec) -> list[dict]:
    return [{"name": name} for name in spec.roles]


def role_mapper(spec: ClientSpec) -> dict:
    """The mapper that puts this client's own roles in its own token.

    All four claim destinations are on because libraries differ in which they
    read -- Grafana, for one, reads roles from the ID token and userinfo rather
    than the access token.
    """
    return {
        "name": ROLE_MAPPER_NAME,
        "protocol": PROTOCOL,
        "protocolMapper": "oidc-usermodel-client-role-mapper",
        "consentRequired": False,
        "config": {
            "usermodel.clientRoleMapping.clientId": spec.client_id,
            "claim.name": spec.role_claim,
            "multivalued": "true",
            "jsonType.label": "String",
            "id.token.claim": "true",
            "access.token.claim": "true",
            "userinfo.token.claim": "true",
            "introspection.token.claim": "true",
        },
    }


def protocol_mappers(spec: ClientSpec) -> list[dict]:
    """Every mapper Scout puts on a fragment's client. One, so far."""
    return [role_mapper(spec)]


def tier_edges(spec: ClientSpec) -> dict[str, list[str]]:
    """Which of this client's roles compose into which tier role.

    No self scope-mapping is needed alongside these: a client's own roles reach
    its own token under `fullScopeAllowed: false` without one, including when
    the role arrives by composite expansion.
    """
    return {tier: list(names) for tier, names in spec.grants.items() if names}


# Fields `client_representation` is authoritative for. Anything else Keycloak
# returns is left alone; comparing it would make every pass a write.
#
# The client-scope lists are deliberately absent. Keycloak applies them on
# create and ignores them on update -- `ClientResource.updateClient` never
# calls `updateClientScopes` -- so managing them would detect drift it could
# never repair and rewrite the client on every pass forever. They are set once
# at creation and are immutable thereafter.
MANAGED_FIELDS = (
    "name",
    "description",
    "enabled",
    "protocol",
    "publicClient",
    "bearerOnly",
    "clientAuthenticatorType",
    "consentRequired",
    "fullScopeAllowed",
    "standardFlowEnabled",
    "implicitFlowEnabled",
    "directAccessGrantsEnabled",
    "serviceAccountsEnabled",
    "redirectUris",
    "webOrigins",
)

# Mapper fields `protocol_mappers` is authoritative for. `name` identifies a
# mapper rather than describing it, and `id` is Keycloak's.
MANAGED_MAPPER_FIELDS = ("protocol", "protocolMapper", "consentRequired", "config")

# Attributes we set. A live client may carry others, which are left alone.
MANAGED_ATTRIBUTES = (
    STAMP_ATTRIBUTE,
    SOURCE_ATTRIBUTE,
    "post.logout.redirect.uris",
    "pkce.code.challenge.method",
)


def client_drift(live: dict, desired: dict) -> list[str]:
    """Which managed fields differ. Empty means no write.

    Order-insensitive on lists: Keycloak does not promise to return
    `redirectUris` in the order it was given.
    """
    out = []
    for field in MANAGED_FIELDS:
        want, have = desired.get(field), live.get(field)
        if isinstance(want, list):
            if sorted(want) != sorted(have or []):
                out.append(field)
        elif want != have:
            out.append(field)
    live_attrs = live.get("attributes") or {}
    want_attrs = desired.get("attributes") or {}
    for key in MANAGED_ATTRIBUTES:
        if want_attrs.get(key) != live_attrs.get(key):
            out.append(f"attributes.{key}")
    return out


def merged_attributes(live: dict, desired: dict) -> dict:
    """Desired attributes over whatever else the live client carries.

    A blind PUT would drop keys Keycloak or an operator added.
    """
    merged = dict(live.get("attributes") or {})
    merged.update(desired.get("attributes") or {})
    return merged


def mapper_drift(live: list[dict], desired: list[dict]) -> tuple[list[dict], list[str]]:
    """(mappers to write, mapper names to delete).

    Compared on the managed fields only: a mapper's type decides what it puts in
    the token, so `config` is not the whole of what it does. An undeclared
    mapper on our client is ours to remove -- the client exists only because of
    this fragment.
    """
    by_name = {m.get("name"): m for m in live}
    write, keep = [], set()
    for want in desired:
        name = want["name"]
        keep.add(name)
        have = by_name.get(name)
        if have is None:
            write.append(want)
        elif any(have.get(f) != want.get(f) for f in MANAGED_MAPPER_FIELDS):
            write.append({**want, "id": have.get("id")})
    delete = [name for name in by_name if name and name not in keep]
    return write, delete
