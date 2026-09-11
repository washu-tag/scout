"""The fragment vocabulary.

A fragment declares Scout concepts, never Keycloak JSON. Every model forbids
unknown fields: that is the primary security control, not a nicety. A field we
did not think about cannot be smuggled through into a client representation,
so the set of things a component can express is exactly the set enumerated
here. Adding a term is the only way to widen it, which makes that widening a
reviewable event.

Field names track Keycloak's Admin API v2 `OIDCClientRepresentation` wherever a
concept exists on both sides -- `clientId`, `displayName`, `redirectUris`,
`loginFlows`, `roles` -- so that if the official per-client CRs ever stabilise,
the migration is a rename rather than a redesign. The Scout-only terms
(`grants`, `roleClaim`) are the ones Keycloak has no client-level form for.

Site portability: a fragment is shipped by a chart that does not know which
Scout site it will be installed on, so URLs carry `${domain}` and the
reconciler substitutes. Baking a hostname into a fragment would make every
pluggable app single-site.
"""

import re
from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .placeholders import OPEN as PLACEHOLDER_OPEN

API_VERSION = "keycloak.scout.xnat.org/v1alpha1"
KIND = "KeycloakFragment"

# Discovery label; a fixed contract, mirrored in the chart and the docs.
FRAGMENT_LABEL = "keycloak.scout.xnat.org/fragment"
FRAGMENT_LABEL_VALUE = "true"

# Keycloak accepts a wide range here; this is only a sanity bound.
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
ROLE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_:-]{0,62}$")
CLAIM_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
DURATION_RE = re.compile(r"^(\d+)([smhd]?)$")

# The closed set of values a fragment may interpolate. Unknown ones are a
# validation error rather than being passed through, so a typo fails offline
# instead of producing a URL nobody meant.
#
# `${domain}` is the reconciler's own interpolation, resolved at compose time
# by `compose.substitute`. Not to be confused with `$(env:...)`, which is
# config-cli's and is resolved inside the apply Job -- see `placeholders`.
# A fragment may write the first and may not write the second.
TEMPLATE_VARS = ("domain",)
TEMPLATE_VAR_RE = re.compile(r"\$\{([^}]*)\}")

# Characters where a browser and `urlparse` read different hosts out of the
# same string. WHATWG ends the authority at a backslash and strips tab, CR and
# LF from a URL entirely, so `https://evil.com\.scout.example.edu/cb` has host
# `scout.example.edu` to `urlparse` -- inside the Scout domain, and therefore
# past `compose.check_host` -- and host `evil.com` in the address bar. Keycloak
# matches a registered redirect URI as a string, so it would hand the code over.
# Nothing legitimate needs any of them in a URL.
AMBIGUOUS_URL_CHARS = frozenset("\\\t\r\n")

# Groups a fragment may grant its own roles into. Not configurable: these two
# are the platform's coarse tiers and nothing else is addressable.
GRANTABLE_GROUPS = ("scout-user", "scout-admin")

# v2's enum, narrowed. IMPLICIT and DIRECT_GRANT are prohibited outright by
# RFC 9700; DEVICE and CIBA are simply not something Scout runs, and an
# unreachable flow is one less thing to reason about.
LoginFlow = Literal["STANDARD", "SERVICE_ACCOUNT", "TOKEN_EXCHANGE"]

# Claims whose meaning is fixed by the JWT/OIDC specs. Writing a client's roles
# into one of these corrupts that client's own tokens -- Keycloak will happily
# emit `sub` twice and the client's own library will pick one.
#
# This is a footgun guard, NOT a security boundary, and it deliberately does not
# try to stop a fragment naming a claim some other Scout service also reads.
# That would be the wrong control: a token is scoped by its audience, and every
# Scout resource server validates one (Trino requires aud=trino, report-viewer
# requires aud=report-viewer). OPA does not read tokens at all -- its user
# attributes come from the Keycloak SPI bundle (ADR 0021). So a claim called
# `groups` in one client's token has no bearing on another's, and a blocklist
# of a few names would be security theatre that only pretends to be a namespace.
STRUCTURAL_CLAIMS = frozenset(
    {"aud", "azp", "exp", "iat", "iss", "jti", "nbf", "sub", "typ"}
)

# There are no per-field count limits, because none would bound anything
# dangerous: a client role is scoped to its client, and each redirect URI is
# host-checked. The bound that matters is on document size, and the discovery
# volume has one.


def strings(value: object, path: str = "") -> Iterator[tuple[str, str]]:
    """Every string anywhere in a model, with the field path that reached it."""
    if isinstance(value, str):
        yield path or "(root)", value
    elif isinstance(value, BaseModel):
        for name, item in value:
            yield from strings(item, f"{path}.{name}" if path else name)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from strings(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from strings(item, f"{path}[{index}]")


def check_no_substitution(model: BaseModel) -> None:
    """Refuse config-cli's substitution prefix anywhere in a fragment.

    The composed realm is applied with `import.var-substitution` on, and
    substitution does not care which part of the document it is reading. A
    fragment that wrote `$(env:oauth2_proxy)` into a display name would have
    oauth2-proxy's client secret resolved into a field it is allowed to read
    back. The reconciler writes the fragment's own `$(env:...)` token itself
    (`placeholders.env_name`), so nothing legitimate needs this syntax.

    Walks every string rather than the four fields that are reachable today:
    this is the security boundary, and a term added later must not quietly opt
    out of it.
    """
    for where, text in strings(model):
        if PLACEHOLDER_OPEN in text:
            raise ValueError(
                f"{where}: {text!r} contains {PLACEHOLDER_OPEN!r}, which the "
                "realm import would resolve as a variable; a fragment may not "
                "use substitution syntax"
            )


def duration_seconds(value: str) -> int:
    match = DURATION_RE.match(value)
    if not match:
        raise ValueError(f"{value!r} is not a duration like 900s, 15m, 8h, 1d")
    amount, unit = int(match.group(1)), match.group(2)
    return amount * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def check_template_vars(text: str, where: str) -> None:
    for name in TEMPLATE_VAR_RE.findall(text):
        if name not in TEMPLATE_VARS:
            raise ValueError(
                f"{where}: unknown template variable ${{{name}}}; "
                f"available: {', '.join('${%s}' % v for v in TEMPLATE_VARS)}"
            )


def check_unambiguous(value: str, where: str) -> None:
    """Refuse a URL whose host a browser and `urlparse` would disagree about."""
    found = sorted(AMBIGUOUS_URL_CHARS & set(value))
    if found:
        raise ValueError(
            f"{where}: {value!r} contains {', '.join(repr(c) for c in found)}, "
            "which a browser reads as ending the hostname and this does not; "
            "a URL may not contain a backslash, tab, carriage return or newline"
        )


def check_url(value: str, where: str) -> None:
    """Syntactic checks only.

    The host cannot be checked here because `${domain}` is not resolved until
    the reconciler knows which site it is running on; `compose.resolve` does
    that half.
    """
    check_template_vars(value, where)
    check_unambiguous(value, where)
    if not value.startswith("https://"):
        raise ValueError(f"{where}: {value!r} must be an https URL")
    if "*" in value:
        raise ValueError(f"{where}: {value!r} must not contain a wildcard")
    remainder = value[len("https://") :]
    if not remainder or remainder.startswith("/"):
        raise ValueError(f"{where}: {value!r} has no host")
    if "@" in remainder.split("/")[0]:
        raise ValueError(f"{where}: {value!r} must not carry userinfo")


class SecretRef(BaseModel):
    """Names the Secret holding this client's credential.

    The Secret lives in the *reconciler's* namespace, not the component's.
    That is what keeps the reconciler's Kubernetes permissions namespace-scoped:
    reading a Secret from an arbitrary namespace would mean cluster-wide secret
    read, which is a far larger grant than anything else in this design. A
    component's chart renders the same value into both places -- its own
    namespace for the app, the reconciler's for the realm apply -- exactly as
    Scout already fans a vault-held client secret out to two consumers today.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    key: str = "client-secret"

    @model_validator(mode="after")
    def _check(self) -> SecretRef:
        if not re.match(r"^[a-z0-9]([a-z0-9.-]{0,61}[a-z0-9])?$", self.name):
            raise ValueError(f"secretRef.name {self.name!r} is not a valid Secret name")
        if not self.key or "/" in self.key or self.key.startswith("."):
            raise ValueError(f"secretRef.key {self.key!r} is not a valid Secret key")
        return self


class ClientSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clientId: str
    displayName: str = ""
    description: str = ""
    loginFlows: list[LoginFlow] = Field(default_factory=lambda: ["STANDARD"])
    appUrl: str = ""
    redirectUris: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    roleClaim: str = "groups"
    grants: dict[Literal["scout-user", "scout-admin"], list[str]] = Field(
        default_factory=dict
    )
    # Keycloak reads this attribute as "this client MUST send a code
    # challenge", not "may". Defaulting it on would break every client whose
    # software does not send one, which is all but one of Scout's today.
    pkce: Literal["off", "required"] = "off"
    accessTokenLifespan: str | None = None
    sessionLifespan: str | None = None
    secretRef: SecretRef

    @model_validator(mode="after")
    def _check(self) -> ClientSpec:
        check_no_substitution(self)
        if not CLIENT_ID_RE.match(self.clientId):
            raise ValueError(
                f"clientId {self.clientId!r} must match {CLIENT_ID_RE.pattern}"
            )
        if not self.loginFlows:
            raise ValueError("loginFlows must not be empty")
        if len(set(self.loginFlows)) != len(self.loginFlows):
            raise ValueError("loginFlows lists a flow twice")
        if "TOKEN_EXCHANGE" in self.loginFlows and "STANDARD" not in self.loginFlows:
            raise ValueError(
                "TOKEN_EXCHANGE requires STANDARD: the exchanging client must be "
                "the one that obtained the subject token"
            )

        interactive = "STANDARD" in self.loginFlows
        if interactive and not self.redirectUris:
            raise ValueError("a STANDARD client must declare at least one redirectUri")
        if not interactive and self.redirectUris:
            raise ValueError(
                "redirectUris are meaningless without STANDARD; a service account "
                "has no browser flow"
            )
        if not interactive and (self.roles or "roleClaim" in self.model_fields_set):
            # Roles reach a token through the user model, and a client with no
            # browser flow issues tokens for nothing but its own service
            # account -- which a fragment cannot grant roles to, since `grants`
            # only addresses the two platform groups. Composing such a client
            # would emit no mapper and no claim, so the component's
            # authorization would fail with nothing anywhere saying why.
            raise ValueError(
                "roles and roleClaim are meaningless without STANDARD; a "
                "service account holds no user's roles"
            )
        for uri in self.redirectUris:
            check_url(uri, "redirectUris")
        if self.appUrl:
            check_url(self.appUrl, "appUrl")

        if not CLAIM_RE.match(self.roleClaim):
            raise ValueError(
                f"roleClaim {self.roleClaim!r} must match {CLAIM_RE.pattern} "
                "(no dots: a nested claim can shadow a structured one)"
            )
        if self.roleClaim in STRUCTURAL_CLAIMS:
            raise ValueError(
                f"roleClaim {self.roleClaim!r} is a registered JWT claim; writing "
                "roles into it would corrupt this client's own tokens"
            )

        if len(set(self.roles)) != len(self.roles):
            raise ValueError("two roles share a name")
        for role in self.roles:
            if not ROLE_RE.match(role):
                raise ValueError(f"role {role!r} must match {ROLE_RE.pattern}")

        declared = set(self.roles)
        for group, granted in self.grants.items():
            for role in granted:
                if role not in declared:
                    raise ValueError(
                        f"grants.{group} names {role!r}, which this client does not "
                        "declare; a fragment may only grant its own roles"
                    )

        for label, value in (
            ("accessTokenLifespan", self.accessTokenLifespan),
            ("sessionLifespan", self.sessionLifespan),
        ):
            if value is not None:
                try:
                    duration_seconds(value)
                except ValueError as exc:
                    raise ValueError(f"{label}: {exc}") from exc
        return self


class Fragment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apiVersion: Literal[API_VERSION]
    kind: Literal[KIND]
    clients: list[ClientSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Fragment:
        if not self.clients:
            raise ValueError("a fragment must declare at least one client")
        ids = [c.clientId for c in self.clients]
        if len(set(ids)) != len(ids):
            raise ValueError("two clients share a clientId")
        return self
