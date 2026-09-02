"""The vocabulary is the security boundary, so these are security tests."""

import pytest
import yaml
from conftest import fragment_yaml
from pydantic import ValidationError

from scout_app_manager.schema import Fragment, duration_seconds


def load(body: str) -> Fragment:
    return Fragment.model_validate(yaml.safe_load(body))


def test_accepts_a_minimal_fragment(hello_yaml):
    fragment = load(hello_yaml)
    client = fragment.clients[0]
    assert client.clientId == "hello"
    assert client.loginFlows == ["STANDARD"]
    assert client.roleClaim == "groups"
    assert client.pkce == "off"
    assert client.secretRef.name == "hello-keycloak-client"


@pytest.mark.parametrize(
    "field,value",
    [
        ("fullScopeAllowed", True),
        ("serviceAccountsEnabled", True),
        ("serviceAccountRoles", ["realm-management"]),
        ("protocolMappers", []),
        ("directAccessGrantsEnabled", True),
        ("publicClient", True),
        ("secret", "hunter2"),
        ("attributes", {}),
        ("defaultClientScopes", ["trino-audience"]),
        ("standardFlowEnabled", False),
    ],
)
def test_rejects_any_raw_keycloak_field(field, value):
    """The whole point: dangerous fields have no syntax."""
    with pytest.raises(ValidationError) as exc:
        load(fragment_yaml(**{field: value}))
    assert "extra_forbidden" in str(exc.value)


def test_rejects_unknown_top_level_key():
    with pytest.raises(ValidationError):
        load(fragment_yaml(top_level={"realm": "scout"}))


def test_a_clientid_is_free():
    """No naming rule and no self-declared owner.

    An `owner` field would have been decorative: a fragment writes whatever it
    likes there, so it could never be evidence of anything. The provenance that
    counts is the source ConfigMap's namespace/name, which the cluster attests.
    """
    assert load(fragment_yaml(client="report_viewer_svc")).clients[0].clientId
    assert load(fragment_yaml(client="anything-at-all")).clients[0].clientId


def test_role_names_carry_no_ownership_rule():
    """A client role already lives under its client, so it cannot collide.

    Requiring a prefix here would break the clients whose consumer software
    dictates the vocabulary: MinIO's `consoleAdmin` is a built-in canned
    policy, and Temporal parses `<namespace>:<role>`.
    """
    assert load(fragment_yaml(name="minio", roles=["consoleAdmin"], grants={}))
    assert load(fragment_yaml(name="temporal", roles=["default:admin"], grants={}))


def test_a_fragment_still_cannot_name_another_clients_roles():
    """The rule that actually matters: grants reference declared roles only."""
    with pytest.raises(ValidationError, match="does not declare"):
        load(
            fragment_yaml(roles=["hello-user"], grants={"scout-admin": ["realm-admin"]})
        )


def test_a_fragment_may_only_grant_its_own_roles():
    with pytest.raises(
        ValidationError, match="does not\n?\\s*declare|does not declare"
    ):
        load(fragment_yaml(grants={"scout-user": ["superset_admin"]}))


def test_grant_targets_are_closed():
    with pytest.raises(ValidationError):
        load(fragment_yaml(grants={"realm-management": ["hello-user"]}))


# --- login flows ---------------------------------------------------------


@pytest.mark.parametrize("flow", ["IMPLICIT", "DIRECT_GRANT", "DEVICE", "CIBA", "junk"])
def test_prohibited_flows_have_no_syntax(flow):
    with pytest.raises(ValidationError):
        load(fragment_yaml(loginFlows=[flow]))


def test_service_account_client_declares_no_redirects():
    fragment = load(
        fragment_yaml(
            loginFlows=["SERVICE_ACCOUNT"], redirectUris=None, roles=[], grants={}
        )
    )
    assert fragment.clients[0].loginFlows == ["SERVICE_ACCOUNT"]


def test_service_account_with_redirects_is_refused():
    with pytest.raises(ValidationError, match="meaningless without STANDARD"):
        load(fragment_yaml(loginFlows=["SERVICE_ACCOUNT"], roles=[], grants={}))


@pytest.mark.parametrize("declared", [{"roles": ["worker"]}, {"roleClaim": "perms"}])
def test_service_account_with_roles_is_refused(declared):
    """Otherwise the claim is silently never emitted.

    A client with no browser flow issues tokens only for its own service
    account, which a fragment cannot grant roles to. Composing it produced no
    mapper at all while `validate` reported the roles and the claim name, so
    the verdict and the applied realm disagreed and the component's
    authorization failed with nothing anywhere saying why.
    """
    spec = {"roles": [], "grants": {}, **declared}
    with pytest.raises(ValidationError, match="meaningless without STANDARD"):
        load(fragment_yaml(loginFlows=["SERVICE_ACCOUNT"], redirectUris=None, **spec))


def test_standard_client_must_declare_a_redirect():
    with pytest.raises(ValidationError, match="at least one redirectUri"):
        load(fragment_yaml(redirectUris=None))


def test_token_exchange_requires_standard():
    with pytest.raises(ValidationError, match="TOKEN_EXCHANGE requires STANDARD"):
        load(fragment_yaml(loginFlows=["TOKEN_EXCHANGE"]))


# --- redirect URIs -------------------------------------------------------


@pytest.mark.parametrize(
    "uri",
    [
        "http://hello.${domain}/cb",
        "https://hello.${domain}/*",
        "https://*.${domain}/cb",
        "https:///cb",
        "https://user:pw@hello.${domain}/cb",
        "/auth/callback",
    ],
)
def test_unsafe_redirect_shapes_are_refused(uri):
    with pytest.raises(ValidationError):
        load(fragment_yaml(redirectUris=[uri]))


def test_unknown_placeholder_is_caught_offline():
    """Without this a typo becomes a URL nobody meant, resolved silently."""
    with pytest.raises(ValidationError, match="unknown placeholder"):
        load(fragment_yaml(redirectUris=["https://hello.${realm_domain}/cb"]))


def test_several_hosts_are_allowed():
    """oauth2-proxy has two; the old subdomain form could not say this."""
    fragment = load(
        fragment_yaml(
            client="oauth2-proxy",
            redirectUris=[
                "https://auth.${domain}/oauth2/callback",
                "https://superset.${domain}/oauth2/idpresponse",
            ],
            roles=[],
            grants={},
        )
    )
    assert len(fragment.clients[0].redirectUris) == 2


def test_apex_domain_is_allowed():
    """launchpad lives at the root; the old subdomain form required one."""
    fragment = load(
        fragment_yaml(
            client="launchpad",
            redirectUris=["https://${domain}/api/auth/callback/keycloak"],
            roles=[],
            grants={},
        )
    )
    assert fragment.clients[0].redirectUris == [
        "https://${domain}/api/auth/callback/keycloak"
    ]


# --- claims and lifespans -------------------------------------------------


@pytest.mark.parametrize("claim", ["sub", "iss", "aud", "exp", "azp"])
def test_registered_jwt_claims_are_refused(claim):
    """A footgun guard, not a security boundary.

    Writing roles into `sub` corrupts this client's own token. It is NOT an
    attempt to stop a fragment naming a claim another service reads -- tokens
    are scoped by audience, every Scout resource server validates one, and OPA
    reads user attributes from its bundle rather than from tokens at all.
    """
    with pytest.raises(ValidationError, match="registered JWT claim"):
        load(fragment_yaml(roleClaim=claim))


def test_a_claim_another_service_also_uses_is_fine(claim="groups"):
    """Six Scout clients already emit `groups`, in six different audiences."""
    assert load(fragment_yaml(roleClaim="groups")).clients[0].roleClaim == "groups"


def test_nested_claim_is_refused():
    with pytest.raises(ValidationError, match="roleClaim"):
        load(fragment_yaml(roleClaim="resource_access.hello.roles"))


def test_non_default_claims_are_allowed():
    """temporal reads `permissions`, minio reads `policy`."""
    assert load(fragment_yaml(roleClaim="permissions")).clients[0].roleClaim
    assert load(fragment_yaml(roleClaim="policy")).clients[0].roleClaim == "policy"


@pytest.mark.parametrize(
    "value,seconds",
    [("900s", 900), ("15m", 900), ("8h", 28800), ("1d", 86400), ("60", 60)],
)
def test_durations_parse(value, seconds):
    assert duration_seconds(value) == seconds


def test_bad_duration_is_refused():
    with pytest.raises(ValidationError, match="not a duration"):
        load(fragment_yaml(accessTokenLifespan="eight hours"))


# --- misc ------------------------------------------------------------------


def test_no_arbitrary_count_limits():
    """They bounded nothing dangerous; the discovery volume bounds size."""
    fragment = load(fragment_yaml(roles=[f"hello-r{i}" for i in range(40)], grants={}))
    assert len(fragment.clients[0].roles) == 40


def test_a_fragment_must_declare_a_client():
    with pytest.raises(ValidationError, match="at least one client"):
        load(fragment_yaml(top_level={"clients": []}))


def test_wrong_api_version_is_refused():
    with pytest.raises(ValidationError):
        load(fragment_yaml(top_level={"apiVersion": "keycloak.scout.xnat.org/v1"}))


def test_secret_ref_is_required():
    """Keycloak-generated secrets are gone; the creator provides one."""
    with pytest.raises(ValidationError):
        load(fragment_yaml(secretRef=None))


def test_secret_ref_defaults_its_key():
    fragment = load(fragment_yaml(secretRef={"name": "hello-keycloak-secret"}))
    assert fragment.clients[0].secretRef.name == "hello-keycloak-secret"
    assert fragment.clients[0].secretRef.key == "client-secret"
