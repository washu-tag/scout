"""Turning fragments into realm objects, and merging them into the base realm.

Everything security-relevant about a fragment client is decided here, not by
the fragment. The fragment says "a browser login for my app with these roles";
this module decides that means confidential, no ROPC, no implicit, full scope
off -- and the self scope-mapping that has to accompany full scope off, without
which the role claim comes out empty and nothing errors.

Two things the fragment declares are checked rather than derived, which is the
change that made the real realm expressible: redirect URIs are written out in
full (with `${domain}` for portability) and this module enforces that every one
of them lands inside the Scout domain. Deriving URLs from a subdomain was
simpler but could not express a client at the apex, a client with two hosts, or
the platform signout URI that eight of nine real clients carry.

No credential is ever written here. A client's `secret` comes out as the
`$(env:...)` token config-cli resolves at import, matching what the base realm
has carried since the Ansible lane stopped inlining its own -- so the composed
realm is an ordinary document that can be published as a ConfigMap, hashed, and
read during an incident without handling secrets. What this module produces
instead is the *binding* from each token to the Secret and key it comes from,
which `apply` turns into Job environment.
"""

import copy
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

from . import substitution
from .load import FragmentRef, LoadedFragment
from .schema import (
    GRANTABLE_GROUPS,
    ClientSpec,
    Fragment,
    PLACEHOLDER_RE,
    duration_seconds,
)

# Marks a client as fragment-created in the composed realm, so the effective
# realm is self-describing when someone reads it during an incident.
#
# The value is the source ConfigMap's namespace/name, which the cluster
# attests via the sidecar's filename -- not a label the fragment chose for
# itself. A self-declared "owner" field would have been decorative here: an
# attacker writes whatever they like in it, so it could never be evidence.
SOURCE_ATTRIBUTE = "scout.fragment.source"

# Keycloak creates these in every realm and the base realm document does not
# list them, so the "already in the base realm" check cannot see them. Without
# this set a fragment could claim `realm-management` and have config-cli prune
# `realm-admin` off it -- taking the Keycloak console away from every Scout
# admin -- or restate `admin-cli` with a secret of its own choosing.
BUILTIN_CLIENTS = frozenset(
    {
        "account",
        "account-console",
        "admin-cli",
        "broker",
        "realm-management",
        "security-admin-console",
    }
)


@dataclass
class ClientEffect:
    """What admitting a fragment does to the realm, in reviewable terms.

    Also the one derivation: `effect_of` builds this, `plan` reports it and
    `_client_representation` renders it, so what the validator describes and
    what reaches the realm cannot drift apart.
    """

    client_id: str
    login_flows: list[str]
    redirect_uris: list[str]
    web_origins: list[str]
    role_claim: str
    roles: list[str] = field(default_factory=list)
    grants: dict[str, list[str]] = field(default_factory=dict)
    secret_source: str = ""
    secret_env: str = ""
    pkce: str = "off"
    lifespans: dict[str, str] = field(default_factory=dict)
    app_url: str = ""

    @property
    def interactive(self) -> bool:
        return "STANDARD" in self.login_flows


@dataclass
class FragmentEffect:
    ref: FragmentRef
    clients: list[ClientEffect] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SecretBinding:
    """Where the value behind one `$(env:...)` token comes from.

    Always a Secret in the reconciler's own namespace, which is what keeps its
    Kubernetes permissions namespace-scoped (see `schema.SecretRef`).
    """

    env: str
    name: str
    key: str


@dataclass
class ComposeResult:
    realm: dict
    accepted: list[LoadedFragment] = field(default_factory=list)
    rejected: list[tuple[LoadedFragment, list[str]]] = field(default_factory=list)
    # env name -> the Secret the apply Job must read it from. Only fragments
    # appear here; the base realm's tokens come from one fixed Secret the Job
    # takes wholesale with envFrom.
    bindings: dict[str, SecretBinding] = field(default_factory=dict)


# Resolves (secretName, key) -> value, from the reconciler's own namespace.
# Passed in rather than imported so composing stays a pure function of its
# inputs and the tests never need a cluster.
SecretResolver = Callable[[str, str], "str | None"]


@dataclass(frozen=True)
class Site:
    """The facts about this Scout install that fragments interpolate against."""

    domain: str
    signout_url: str = ""

    def values(self) -> dict[str, str]:
        return {"domain": self.domain}

    def platform_signout(self) -> str:
        return self.signout_url or f"https://auth.{self.domain}/oauth2/sign_out"


def substitute(text: str, site: Site) -> str:
    values = site.values()
    return PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], text)


def check_host(uri: str, site: Site) -> None:
    """Every redirect target must land inside the Scout domain.

    This is the property `subdomain` used to buy by construction. Enforcing it
    as a check instead costs one function and buys back every URL shape the
    real realm uses.
    """
    host = (urlparse(uri).hostname or "").lower()
    domain = site.domain.lower()
    if host != domain and not host.endswith(f".{domain}"):
        raise ValueError(
            f"redirect URI {uri!r} points at {host or '(no host)'}, which is "
            f"outside {site.domain}"
        )


def resolve_client(spec: ClientSpec, site: Site) -> tuple[list[str], list[str], str]:
    """Return (redirectUris, webOrigins, appUrl) with placeholders resolved."""
    uris = []
    for raw in spec.redirectUris:
        uri = substitute(raw, site)
        check_host(uri, site)
        uris.append(uri)

    app_url = substitute(spec.appUrl, site) if spec.appUrl else ""
    if app_url:
        check_host(app_url, site)

    # Derived, never declared: every real client's web origins are exactly the
    # origins of its own redirect URIs, including oauth2-proxy with its two.
    origins = []
    for uri in uris:
        parsed = urlparse(uri)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in origins:
            origins.append(origin)
    return uris, origins, app_url


def effect_of(spec: ClientSpec, site: Site) -> ClientEffect:
    """Resolve one client spec against this site. Raises on a bad redirect URI."""
    uris, origins, app_url = resolve_client(spec, site)
    if "STANDARD" in spec.loginFlows:
        # The signout URI is a platform fact. Making every chart hardcode
        # oauth2-proxy's subdomain would be exactly the coupling fragments
        # exist to remove.
        uris = uris + [site.platform_signout()]
    lifespans = {}
    if spec.accessTokenLifespan:
        lifespans["access token"] = spec.accessTokenLifespan
    if spec.sessionLifespan:
        lifespans["session"] = spec.sessionLifespan
    return ClientEffect(
        client_id=spec.clientId,
        login_flows=list(spec.loginFlows),
        redirect_uris=uris,
        web_origins=origins,
        role_claim=spec.roleClaim,
        roles=list(spec.roles),
        grants={g: list(r) for g, r in spec.grants.items()},
        secret_source=f"{spec.secretRef.name}/{spec.secretRef.key}",
        secret_env=substitution.env_name(spec.clientId),
        pkce=spec.pkce,
        lifespans=lifespans,
        app_url=app_url,
    )


def plan(loaded: LoadedFragment, site: Site) -> FragmentEffect:
    """Describe a fragment's realm effect without touching a realm.

    What `validate` and `status` report: "this creates client X with these
    redirect URIs, these roles, and grants role R to everyone in scout-user"
    rather than the raw YAML.
    """
    fragment = loaded.fragment
    effect = FragmentEffect(ref=loaded.ref)
    if fragment is None:
        return effect
    for spec in fragment.clients:
        try:
            effect.clients.append(effect_of(spec, site))
        except ValueError as exc:
            effect.errors.append(str(exc))
    return effect


def compose(
    base_realm: dict,
    fragments: list[LoadedFragment],
    site: Site,
    resolve_secret: SecretResolver | None = None,
    reserved_env: frozenset[str] = frozenset(),
) -> ComposeResult:
    """Merge discovered fragments into a copy of the base realm.

    Fail-closed per fragment: anything that cannot be composed safely is
    rejected with a reason and left out, while the base realm and every other
    fragment still compose. A broken fragment breaks its own component's auth,
    loudly, and nothing else.

    `reserved_env` is the base realm's own substitution variables. A fragment
    whose derived name lands on one of them is rejected rather than allowed to
    redirect a platform client's credential.
    """
    realm = copy.deepcopy(base_realm)
    result = ComposeResult(realm=realm)

    existing_clients = {
        c.get("clientId") for c in base_realm.get("clients", []) if isinstance(c, dict)
    }
    groups_by_name = {
        g.get("name"): g for g in realm.get("groups", []) if isinstance(g, dict)
    }

    candidates = sorted(fragments, key=lambda f: f.ref)
    rejected: dict[FragmentRef, list[str]] = {}

    for loaded in candidates:
        if not loaded.valid:
            rejected[loaded.ref] = loaded.errors or ["invalid fragment"]

    # Cross-fragment collisions reject every party. Nobody wins a race for a
    # name, because "whoever reconciled first" is not a property anyone can
    # reason about at review time.
    claim_owners: dict[str, list[FragmentRef]] = {}
    env_owners: dict[str, list[FragmentRef]] = {}
    for loaded in candidates:
        if loaded.ref in rejected or loaded.fragment is None:
            continue
        for spec in loaded.fragment.clients:
            claim_owners.setdefault(spec.clientId, []).append(loaded.ref)
            env_owners.setdefault(substitution.env_name(spec.clientId), []).append(
                loaded.ref
            )

    for client_id, refs in claim_owners.items():
        if len(refs) > 1:
            for ref in refs:
                rejected.setdefault(ref, []).append(
                    f"client {client_id!r} is also declared by "
                    + ", ".join(str(r) for r in refs if r != ref)
                )

    # Distinct clientIds can still land on one variable name -- `a-b` and `a.b`
    # both sanitise to `fragment_a_b` -- and whichever the Job set last would
    # hand its credential to both clients.
    for env, refs in env_owners.items():
        if len(set(refs)) > 1:
            for ref in set(refs):
                rejected.setdefault(ref, []).append(
                    f"the credential variable {env!r} is also claimed by "
                    + ", ".join(str(r) for r in sorted(set(refs)) if r != ref)
                )

    # What each admitted fragment resolved to, kept from the validation pass:
    # composing it again would mean a second GET for every client's Secret.
    admitted: dict[FragmentRef, list[ClientEffect]] = {}

    for loaded in candidates:
        if loaded.ref in rejected or loaded.fragment is None:
            continue
        problems: list[str] = []
        effects: list[ClientEffect] = []
        for spec in loaded.fragment.clients:
            if spec.clientId in existing_clients:
                problems.append(
                    f"client {spec.clientId!r} already exists in the base realm; a "
                    "fragment may not adopt a platform-owned client"
                )
            elif spec.clientId in BUILTIN_CLIENTS:
                problems.append(
                    f"client {spec.clientId!r} is one of Keycloak's built-in "
                    "clients; a fragment may not adopt a platform-owned client"
                )
            env = substitution.env_name(spec.clientId)
            if env in reserved_env:
                problems.append(
                    f"client {spec.clientId!r} resolves to the credential variable "
                    f"{env!r}, which the base realm already uses"
                )
            try:
                effects.append(effect_of(spec, site))
            except ValueError as exc:
                problems.append(str(exc))
            # Read only to prove there is something to read. The value stays in
            # the Secret; the document gets the token, and the apply Job gets a
            # secretKeyRef. An absent or empty one would be substituted as a
            # blank credential, or left as the literal token, with no error.
            if resolve_secret is not None:
                ref = spec.secretRef
                if not resolve_secret(ref.name, ref.key):
                    problems.append(
                        f"client {spec.clientId!r} references secret "
                        f"{ref.name}/{ref.key}, which is missing or empty in the "
                        "reconciler's namespace"
                    )
            for group in spec.grants:
                if group not in groups_by_name:
                    problems.append(
                        f"cannot grant into {group!r}: no such group in the base realm"
                    )
        if problems:
            rejected[loaded.ref] = problems
        else:
            admitted[loaded.ref] = effects

    for loaded in candidates:
        if loaded.ref in rejected or loaded.fragment is None:
            continue
        _apply(
            realm,
            loaded.fragment,
            str(loaded.ref),
            groups_by_name,
            admitted[loaded.ref],
        )
        for spec in loaded.fragment.clients:
            env = substitution.env_name(spec.clientId)
            result.bindings[env] = SecretBinding(
                env=env, name=spec.secretRef.name, key=spec.secretRef.key
            )
        result.accepted.append(loaded)

    result.rejected = [
        (loaded, rejected[loaded.ref])
        for loaded in candidates
        if loaded.ref in rejected
    ]
    return result


def _apply(
    realm: dict,
    fragment: Fragment,
    source: str,
    groups_by_name: dict[str, dict],
    effects: list[ClientEffect],
) -> None:
    clients = realm.setdefault("clients", [])
    roles = realm.setdefault("roles", {}).setdefault("client", {})
    scope_mappings = realm.setdefault("clientScopeMappings", {})

    for spec, effect in zip(fragment.clients, effects, strict=True):
        clients.append(_client_representation(spec, effect, source))

        if spec.roles:
            roles[spec.clientId] = [
                {
                    "name": role,
                    "description": f"{role} (from {source})",
                    "composite": False,
                    "clientRole": True,
                    "attributes": {},
                }
                for role in spec.roles
            ]
            # fullScopeAllowed=false does NOT leave a client in scope for its
            # own roles. Without this entry the role mapper emits an empty
            # claim and Keycloak reports no error at all.
            scope_mappings[spec.clientId] = [
                {"client": spec.clientId, "roles": list(spec.roles)}
            ]

        for group_name, granted in spec.grants.items():
            group = groups_by_name[group_name]
            group.setdefault("clientRoles", {}).setdefault(spec.clientId, []).extend(
                granted
            )


def _client_representation(
    spec: ClientSpec,
    effect: ClientEffect,
    source: str,
) -> dict:
    """Render the effect `plan` reported. Nothing about the site is derived here."""
    interactive = effect.interactive
    attributes: dict[str, str] = {SOURCE_ATTRIBUTE: source}
    if spec.pkce == "required":
        attributes["pkce.code.challenge.method"] = "S256"
    if "TOKEN_EXCHANGE" in spec.loginFlows:
        attributes["standard.token.exchange.enabled"] = "true"
    if spec.accessTokenLifespan:
        attributes["access.token.lifespan"] = str(
            duration_seconds(spec.accessTokenLifespan)
        )
    if spec.sessionLifespan:
        seconds = str(duration_seconds(spec.sessionLifespan))
        attributes["client.session.idle.timeout"] = seconds
        attributes["client.session.max.lifespan"] = seconds
        attributes["client.offline.session.idle.timeout"] = seconds

    representation = {
        "clientId": spec.clientId,
        "name": spec.displayName or spec.clientId,
        "description": spec.description or f"{spec.clientId} (from {source})",
        "enabled": True,
        "protocol": "openid-connect",
        "publicClient": False,
        "clientAuthenticatorType": "client-secret",
        "standardFlowEnabled": interactive,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": "SERVICE_ACCOUNT" in spec.loginFlows,
        "fullScopeAllowed": False,
        "attributes": attributes,
    }
    if effect.app_url:
        representation["rootUrl"] = effect.app_url
    # Named, never inlined -- the same form the base realm's own clients use.
    # `compose` has already proven there is a non-empty value behind it, and
    # publishes the binding the apply Job needs to put it in config-cli's
    # environment.
    representation["secret"] = substitution.placeholder(effect.secret_env)
    if effect.redirect_uris:
        representation["redirectUris"] = effect.redirect_uris
    if effect.web_origins:
        representation["webOrigins"] = effect.web_origins

    # Scope lists are deliberately absent. A newly created client is seeded
    # from the realm's defaultDefaultClientScopes, so restating the same six
    # would be noise -- but note the trade: config-cli does not reconcile
    # scopes for a client that declares none, so a stray attachment on such a
    # client would persist. Safe while fragment clients only ever hold the
    # default set; revisit when capabilities start attaching scopes.
    # Roles imply STANDARD; the schema refuses them on any other client, so
    # this cannot silently drop a claim the fragment declared.
    if spec.roles:
        representation["protocolMappers"] = [
            {
                "name": "client-roles",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-usermodel-client-role-mapper",
                "consentRequired": False,
                "config": {
                    "usermodel.clientRoleMapping.clientId": spec.clientId,
                    "claim.name": spec.roleClaim,
                    "multivalued": "true",
                    "jsonType.label": "String",
                    "id.token.claim": "true",
                    "access.token.claim": "true",
                    "userinfo.token.claim": "true",
                    "introspection.token.claim": "true",
                },
            }
        ]
    else:
        representation["protocolMappers"] = []
    return representation


def canonical(realm: dict) -> str:
    return json.dumps(realm, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def document_hash(text: str) -> str:
    """sha256 over the exact bytes that reach config-cli.

    Also what config-cli records in the realm's import-checksum attribute, so
    `keycloak` can compare the two without reading anything back.
    """
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def realm_hash(realm: dict) -> str:
    return document_hash(canonical(realm))


def fragment_client_ids(realm: dict) -> dict[str, str]:
    """clientId -> source ref, for every fragment-created client in a realm."""
    out = {}
    for client in realm.get("clients", []):
        if not isinstance(client, dict):
            continue
        source = (client.get("attributes") or {}).get(SOURCE_ATTRIBUTE)
        if source:
            out[client["clientId"]] = source
    return out


__all__ = [
    "BUILTIN_CLIENTS",
    "ClientEffect",
    "ComposeResult",
    "FragmentEffect",
    "GRANTABLE_GROUPS",
    "SecretBinding",
    "Site",
    "canonical",
    "compose",
    "document_hash",
    "fragment_client_ids",
    "plan",
    "realm_hash",
    "resolve_client",
]
