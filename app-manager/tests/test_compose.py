import copy
import json

import pytest
from conftest import fragment_yaml, write_fragment

from scout_app_manager.compose import (
    BUILTIN_CLIENTS,
    SecretBinding,
    Site,
    canonical,
    compose,
    plan,
)
from scout_app_manager.load import scan


def composed(base_realm, tmp_path, domain="scout.example.edu"):
    return compose(base_realm, scan(tmp_path), Site(domain=domain))


def client_of(realm, client_id):
    return next(c for c in realm["clients"] if c["clientId"] == client_id)


def group_of(realm, name):
    return next(g for g in realm["groups"] if g["name"] == name)


def test_no_fragments_is_a_byte_identical_realm(base_realm, tmp_path):
    """The invariant a first deploy has to satisfy."""
    before = canonical(base_realm)
    result = composed(base_realm, tmp_path)
    assert canonical(result.realm) == before
    assert result.accepted == []


def test_invariants_are_injected_not_declared(base_realm, tmp_path, hello_yaml):
    write_fragment(tmp_path, "scout-demo", "hello-fragment", hello_yaml)
    realm = composed(base_realm, tmp_path).realm
    client = client_of(realm, "hello")

    assert client["fullScopeAllowed"] is False
    assert client["publicClient"] is False
    assert client["directAccessGrantsEnabled"] is False
    assert client["implicitFlowEnabled"] is False
    assert client["serviceAccountsEnabled"] is False
    assert client["redirectUris"] == [
        "https://hello.scout.example.edu/auth/callback",
        "https://auth.scout.example.edu/oauth2/sign_out",
    ]
    assert client["webOrigins"] == ["https://hello.scout.example.edu"]


def test_pkce_is_opt_in_because_enforcing_it_breaks_real_clients(
    base_realm, tmp_path, hello_yaml
):
    """The attribute means "must send a challenge", not "may".

    Exactly one of Scout's twelve clients sets it, so injecting it everywhere
    would break the other eleven's logins at adoption.
    """
    write_fragment(tmp_path, "scout-demo", "off", hello_yaml)
    client = client_of(composed(base_realm, tmp_path).realm, "hello")
    assert "pkce.code.challenge.method" not in client["attributes"]


def test_pkce_is_enforced_when_asked_for(base_realm, tmp_path):
    write_fragment(
        tmp_path, "scout-demo", "on", fragment_yaml("hello", pkce="required")
    )
    client = client_of(composed(base_realm, tmp_path).realm, "hello")
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"


def test_a_redirect_outside_the_scout_domain_is_refused(base_realm, tmp_path):
    """The check that replaced deriving URLs from a subdomain."""
    write_fragment(
        tmp_path,
        "evil",
        "exfil",
        fragment_yaml("hello", redirectUris=["https://hello.evil.example/cb"]),
    )
    result = composed(base_realm, tmp_path)
    assert result.accepted == []
    assert "outside scout.example.edu" in " ".join(result.rejected[0][1])


def test_a_lookalike_host_is_refused(base_realm, tmp_path):
    """`<domain>.evil.example` must not pass a naive suffix test."""
    write_fragment(
        tmp_path,
        "evil",
        "lookalike",
        fragment_yaml(
            "hello", redirectUris=["https://scout.example.edu.evil.example/cb"]
        ),
    )
    result = composed(base_realm, tmp_path)
    assert result.accepted == []


def test_scope_lists_are_omitted_so_the_realm_default_seeds_them(
    base_realm, tmp_path, hello_yaml
):
    write_fragment(tmp_path, "scout-demo", "hello-fragment", hello_yaml)
    client = client_of(composed(base_realm, tmp_path).realm, "hello")
    assert "defaultClientScopes" not in client
    assert "optionalClientScopes" not in client


def test_self_scope_mapping_accompanies_full_scope_off(
    base_realm, tmp_path, hello_yaml
):
    """Without this the role claim is empty and nothing reports an error."""
    write_fragment(tmp_path, "scout-demo", "hello-fragment", hello_yaml)
    realm = composed(base_realm, tmp_path).realm
    assert realm["clientScopeMappings"]["hello"] == [
        {"client": "hello", "roles": ["hello-user", "hello-admin"]}
    ]


def test_role_mapper_writes_the_requested_claim(base_realm, tmp_path, hello_yaml):
    write_fragment(tmp_path, "scout-demo", "hello-fragment", hello_yaml)
    client = client_of(composed(base_realm, tmp_path).realm, "hello")
    mapper = client["protocolMappers"][0]
    assert mapper["protocolMapper"] == "oidc-usermodel-client-role-mapper"
    assert mapper["config"]["claim.name"] == "groups"
    assert mapper["config"]["usermodel.clientRoleMapping.clientId"] == "hello"


def test_grants_land_in_the_named_groups_only(base_realm, tmp_path, hello_yaml):
    write_fragment(tmp_path, "scout-demo", "hello-fragment", hello_yaml)
    realm = composed(base_realm, tmp_path).realm
    assert group_of(realm, "scout-user")["clientRoles"]["hello"] == ["hello-user"]
    assert group_of(realm, "scout-admin")["clientRoles"]["hello"] == ["hello-admin"]
    # existing grants survive
    assert group_of(realm, "scout-user")["clientRoles"]["launchpad"] == [
        "launchpad-user"
    ]


def test_base_realm_is_not_mutated(base_realm, tmp_path, hello_yaml):
    write_fragment(tmp_path, "scout-demo", "hello-fragment", hello_yaml)
    untouched = copy.deepcopy(base_realm)
    composed(base_realm, tmp_path)
    assert base_realm == untouched


def test_a_fragment_cannot_adopt_a_platform_client(base_realm, tmp_path):
    body = fragment_yaml(name="launchpad", roles=[], grants={})
    write_fragment(tmp_path, "evil", "takeover", body)
    result = composed(base_realm, tmp_path)
    assert result.accepted == []
    assert "already exists in the base realm" in result.rejected[0][1][0]


@pytest.mark.parametrize("builtin", sorted(BUILTIN_CLIENTS))
def test_a_fragment_cannot_adopt_a_keycloak_builtin_client(
    base_realm, tmp_path, builtin
):
    """The base realm never lists these, so the collision check cannot see them.

    `realm-management` is the one that costs: the apply Job prunes roles with
    `role: full`, so adopting it would strip `realm-admin` and take the
    Keycloak console away from every Scout admin.
    """
    write_fragment(tmp_path, "evil", "takeover", fragment_yaml(client=builtin))
    result = composed(base_realm, tmp_path)

    assert result.accepted == []
    assert "built-in" in " ".join(result.rejected[0][1])
    assert not any(c["clientId"] == builtin for c in result.realm["clients"])
    assert builtin not in result.realm["roles"]["client"]


def test_two_fragments_claiming_one_client_reject_both(
    base_realm, tmp_path, hello_yaml
):
    write_fragment(tmp_path, "team-a", "hello-fragment", hello_yaml)
    write_fragment(tmp_path, "team-b", "hello-fragment", hello_yaml)
    result = composed(base_realm, tmp_path)
    assert result.accepted == []
    assert len(result.rejected) == 2
    assert all("also declared by" in " ".join(r) for _, r in result.rejected)


def test_one_bad_fragment_does_not_stop_the_others(base_realm, tmp_path, hello_yaml):
    write_fragment(tmp_path, "scout-demo", "hello-fragment", hello_yaml)
    write_fragment(tmp_path, "scout-bad", "broken", "owner: [not, a, string")
    result = composed(base_realm, tmp_path)
    assert [str(f.ref) for f in result.accepted] == ["scout-demo/hello-fragment"]
    assert [str(f.ref) for f, _ in result.rejected] == ["scout-bad/broken"]
    assert client_of(result.realm, "hello")


def test_a_configmap_whose_keys_collide_rejects_only_itself(
    base_realm, tmp_path, hello_yaml
):
    """Cross-key validation must not escape scan(): it would kill the reconcile.

    Each document validates alone, so the failure only appears when the
    ConfigMap's keys are merged -- after the per-document guard.
    """
    for key in ("a", "b"):
        (tmp_path / f"namespace_scout-bad.configmap_twice.{key}.yaml").write_text(
            fragment_yaml("dup"), encoding="utf-8"
        )
    write_fragment(tmp_path, "scout-demo", "hello-fragment", hello_yaml)

    loaded = scan(tmp_path)
    result = compose(base_realm, loaded, Site(domain="scout.example.edu"))

    assert [str(f.ref) for f in result.accepted] == ["scout-demo/hello-fragment"]
    assert [str(f.ref) for f, _ in result.rejected] == ["scout-bad/twice"]
    assert "two clients share a clientId" in " ".join(result.rejected[0][1])


def test_unknown_field_rejects_only_its_own_fragment(base_realm, tmp_path, hello_yaml):
    write_fragment(tmp_path, "scout-demo", "hello-fragment", hello_yaml)
    write_fragment(
        tmp_path,
        "scout-bad",
        "sneaky",
        fragment_yaml(name="sneaky", fullScopeAllowed=True),
    )
    result = composed(base_realm, tmp_path)
    assert [str(f.ref) for f in result.accepted] == ["scout-demo/hello-fragment"]
    reasons = " ".join(result.rejected[0][1])
    assert "vocabulary is closed" in reasons


def test_grant_to_a_missing_group_is_rejected(tmp_path, hello_yaml):
    realm_without_groups = {"realm": "scout", "clients": [], "groups": []}
    write_fragment(tmp_path, "scout-demo", "hello-fragment", hello_yaml)
    result = compose(
        realm_without_groups, scan(tmp_path), Site(domain="scout.example.edu")
    )
    assert result.accepted == []
    assert "no such group" in " ".join(result.rejected[0][1])


def test_provenance_comes_from_the_sidecar_filename(tmp_path, hello_yaml):
    write_fragment(tmp_path, "scout-demo", "hello-fragment", hello_yaml)
    loaded = scan(tmp_path)[0]
    assert loaded.ref.namespace == "scout-demo"
    assert loaded.ref.name == "hello-fragment"
    assert loaded.content_hash.startswith("sha256:")


def test_content_hash_changes_when_the_fragment_changes(tmp_path, hello_yaml):
    write_fragment(tmp_path, "scout-demo", "hello-fragment", hello_yaml)
    first = scan(tmp_path)[0].content_hash
    write_fragment(
        tmp_path, "scout-demo", "hello-fragment", hello_yaml + "\n# a comment\n"
    )
    assert scan(tmp_path)[0].content_hash != first


def test_plan_describes_the_effect_for_review(tmp_path, hello_yaml):
    write_fragment(tmp_path, "scout-demo", "hello-fragment", hello_yaml)
    effect = plan(scan(tmp_path)[0], Site(domain="scout.example.edu"))
    assert str(effect.ref) == "scout-demo/hello-fragment"
    client = effect.clients[0]
    assert client.roles == ["hello-user", "hello-admin"]
    assert client.grants == {
        "scout-user": ["hello-user"],
        "scout-admin": ["hello-admin"],
    }
    assert client.secret_source == "hello-keycloak-client/client-secret"


def test_the_referenced_secret_is_named_not_inlined(base_realm, tmp_path, hello_yaml):
    """The document names the credential; config-cli resolves it at import.

    Same shape as the base realm's own clients, and what lets the composed
    realm be published as a ConfigMap.
    """
    write_fragment(tmp_path, "scout-demo", "hello-fragment", hello_yaml)
    calls = []

    def resolve(name, key):
        calls.append((name, key))
        return "s3cret" if name == "hello-keycloak-client" else None

    result = compose(
        base_realm, scan(tmp_path), Site(domain="scout.example.edu"), resolve
    )

    assert client_of(result.realm, "hello")["secret"] == "$(env:fragment_hello)"
    assert "s3cret" not in json.dumps(result.realm)
    assert result.bindings == {
        "fragment_hello": SecretBinding(
            env="fragment_hello", name="hello-keycloak-client", key="client-secret"
        )
    }
    # Still read once per client per reconcile -- to prove there is a value,
    # not to copy it. Each call is an API round-trip.
    assert calls == [("hello-keycloak-client", "client-secret")]


def test_two_clients_that_share_a_variable_are_both_rejected(base_realm, tmp_path):
    """`a_b` and `a-b` are distinct clientIds and one environment variable.

    Nobody wins the race, for the same reason a clientId collision rejects
    every party: whichever the Job set last would hand its credential to both.
    """
    write_fragment(tmp_path, "one", "f", fragment_yaml(client="shared_app"))
    write_fragment(tmp_path, "two", "f", fragment_yaml(client="shared-app"))

    result = compose(
        base_realm, scan(tmp_path), Site(domain="scout.example.edu"), lambda n, k: "s"
    )

    assert result.accepted == []
    assert len(result.rejected) == 2
    for _, reasons in result.rejected:
        assert any("fragment_shared_app" in reason for reason in reasons)


def test_two_clients_in_one_configmap_that_share_a_variable_are_rejected(
    base_realm, tmp_path
):
    """One source, so there is no other fragment to name in the reason.

    Rejecting only on distinct sources would have let this one through, and
    the two clients would then have shared whichever credential the Job set
    last.
    """
    write_fragment(tmp_path, "one", "f", fragment_yaml(client="shared_app"))
    (tmp_path / "namespace_one.configmap_f.second.yaml").write_text(
        fragment_yaml(client="shared-app"), encoding="utf-8"
    )

    result = compose(
        base_realm, scan(tmp_path), Site(domain="scout.example.edu"), lambda n, k: "s"
    )

    assert result.accepted == []
    assert len(result.rejected) == 1
    assert any("fragment_shared_app" in reason for reason in result.rejected[0][1])


def test_a_fragment_cannot_claim_a_base_realm_variable(base_realm, tmp_path):
    """The `fragment_` prefix keeps Scout's own keys apart; a site's it cannot.

    This is the backstop for an operator who puts a `fragment_`-prefixed key
    into keycloak-client-secrets: the apply Job's per-client secretKeyRef wins
    over envFrom for the same name, so any base-realm client naming that
    variable is quietly installed with the fragment's credential.
    """
    write_fragment(tmp_path, "scout-demo", "f", fragment_yaml(client="hello"))

    result = compose(
        base_realm,
        scan(tmp_path),
        Site(domain="scout.example.edu"),
        lambda n, k: "s",
        reserved_env=frozenset({"fragment_hello"}),
    )

    assert result.accepted == []
    assert "client-secrets Secret already defines" in result.rejected[0][1][0]


def test_a_missing_secret_rejects_the_fragment(base_realm, tmp_path, hello_yaml):
    """Loudly, rather than creating a client nobody can authenticate as."""
    write_fragment(tmp_path, "scout-demo", "hello-fragment", hello_yaml)
    result = compose(
        base_realm, scan(tmp_path), Site(domain="scout.example.edu"), lambda n, k: None
    )
    assert result.accepted == []
    assert "missing or empty" in " ".join(result.rejected[0][1])


def test_the_source_attribute_is_the_attested_ref(base_realm, tmp_path, hello_yaml):
    """Provenance a fragment cannot lie about.

    The value comes from the sidecar's filename, which the cluster wrote from
    the real ConfigMap -- not from a field the fragment filled in itself.
    """
    write_fragment(tmp_path, "scout-demo", "hello-fragment", hello_yaml)
    realm = composed(base_realm, tmp_path).realm
    attributes = client_of(realm, "hello")["attributes"]
    assert attributes["scout.fragment.source"] == "scout-demo/hello-fragment"


def test_two_namespaces_may_ship_unrelated_clients(base_realm, tmp_path):
    """Nothing ties a fragment to a name any more, so this is simply fine."""
    write_fragment(tmp_path, "team-a", "one", fragment_yaml(client="alpha"))
    write_fragment(tmp_path, "team-b", "two", fragment_yaml(client="beta"))
    result = composed(base_realm, tmp_path)
    assert len(result.accepted) == 2
    assert client_of(result.realm, "alpha") and client_of(result.realm, "beta")
