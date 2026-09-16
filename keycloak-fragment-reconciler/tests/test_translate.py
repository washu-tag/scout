"""What a fragment becomes, pinned to a file.

`translate` is the module a Keycloak upgrade churns, so an exact record of the
representations we send is worth having. No Kubernetes, no Keycloak, no loop.

Regenerate with `PYTEST_GOLDEN_UPDATE=1 pytest tests/test_translate.py`, and
read the diff -- an unexplained change here changes what every app's client
looks like.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from conftest import HOSTNAME, fragment_text

from scout_keycloak_fragment_reconciler import translate
from scout_keycloak_fragment_reconciler.fragment import parse

GOLDEN = Path(__file__).parent / "golden"

# Copied from the base realm's own clients. A failure here after a realm edit
# means a fragment's client and a hand-written one have diverged on scopes.
REALM_DEFAULT_SCOPES = ["web-origins", "acr", "profile", "roles", "basic", "email"]
REALM_OPTIONAL_SCOPES = ["address", "phone", "organization", "offline_access"]

# Every field a fragment must carry, and not one more.
MINIMAL = f"""
apiVersion: keycloak.scout.xnat.org/v1alpha1
kind: KeycloakFragment
clients:
  - clientId: minimal
    displayName: Minimal
    appUrl: https://minimal.{HOSTNAME}
    redirectUris: [https://minimal.{HOSTNAME}/cb]
    secretRef: {{name: minimal-secret}}
"""


def rendered(text: str, *, source: str = "demo/hello-keycloak") -> dict:
    spec = parse(text).clients[0]
    return {
        "client": translate.client_representation(
            spec, secret="REDACTED", source=source
        ),
        "roles": translate.role_representations(spec),
        "protocolMappers": translate.protocol_mappers(spec),
        "tierEdges": translate.tier_edges(spec),
    }


def assert_golden(name: str, actual: dict) -> None:
    path = GOLDEN / f"{name}.json"
    serialized = json.dumps(actual, indent=2, sort_keys=True) + "\n"
    if os.environ.get("PYTEST_GOLDEN_UPDATE"):
        path.parent.mkdir(exist_ok=True)
        path.write_text(serialized, encoding="utf-8")
        pytest.skip(f"updated {path.name}")
    assert path.exists(), f"missing golden file {path}; run with PYTEST_GOLDEN_UPDATE=1"
    assert json.loads(serialized) == json.loads(path.read_text(encoding="utf-8"))


class TestGolden:
    def test_hello_scout(self):
        assert_golden("hello-scout", rendered(fragment_text()))

    def test_minimal(self):
        assert_golden("minimal", rendered(MINIMAL))


class TestScoutDecidesNotTheApp:
    """The fields a fragment has no syntax for, asserted rather than assumed."""

    def test_the_closed_capability_set(self):
        client = rendered(fragment_text())["client"]
        assert client["fullScopeAllowed"] is False
        assert client["publicClient"] is False
        assert client["implicitFlowEnabled"] is False
        assert client["directAccessGrantsEnabled"] is False
        assert client["serviceAccountsEnabled"] is False
        assert client["standardFlowEnabled"] is True

    def test_the_scope_sets_match_the_base_realm(self):
        client = rendered(fragment_text())["client"]
        assert client["defaultClientScopes"] == REALM_DEFAULT_SCOPES
        assert client["optionalClientScopes"] == REALM_OPTIONAL_SCOPES

    def test_the_role_mapper_matches_the_realms_own(self):
        mapper = rendered(fragment_text())["protocolMappers"][0]
        assert mapper["protocolMapper"] == "oidc-usermodel-client-role-mapper"
        assert mapper["config"] == {
            "usermodel.clientRoleMapping.clientId": "hello",
            "claim.name": "groups",
            "multivalued": "true",
            "jsonType.label": "String",
            "id.token.claim": "true",
            "access.token.claim": "true",
            "userinfo.token.claim": "true",
            "introspection.token.claim": "true",
        }

    def test_pkce_s256_is_unconditional(self):
        """No fragment can turn this off, including the minimal one."""
        for text in (fragment_text(), MINIMAL):
            client = rendered(text)["client"]
            assert client["attributes"]["pkce.code.challenge.method"] == "S256"

    def test_no_self_scope_mapping_is_produced(self):
        """A client's own roles reach its own token under `fullScopeAllowed:
        false` without one, so there is one fewer object to own."""
        assert "scopeMappings" not in rendered(fragment_text())
        surface = {n for n in dir(translate) if not n.startswith("_")}
        assert not [n for n in surface if "scope_mapping" in n]


class TestStamp:
    def test_the_stamp_and_the_source_are_both_set(self):
        client = rendered(fragment_text(), source="ns/cm")["client"]
        assert client["attributes"][translate.STAMP_ATTRIBUTE] == "true"
        assert client["attributes"][translate.SOURCE_ATTRIBUTE] == "ns/cm"

    def test_ownership_reads_the_stamp_alone(self):
        """Not the source, which is what makes a rename an update."""
        moved = {"attributes": {translate.STAMP_ATTRIBUTE: "true"}}
        assert translate.is_ours(moved)
        assert translate.source_of(moved) == ""

    def test_an_unstamped_client_is_never_ours(self):
        assert not translate.is_ours({})
        assert not translate.is_ours({"attributes": {}})
        assert not translate.is_ours({"attributes": {"owner": "the base realm"}})
        # Not a boolean coercion: only the exact string counts.
        assert not translate.is_ours(
            {"attributes": {translate.STAMP_ATTRIBUTE: "false"}}
        )


class TestDrift:
    def test_identical_is_no_drift(self):
        client = rendered(fragment_text())["client"]
        assert translate.client_drift(client, client) == []

    def test_unmanaged_keycloak_fields_are_not_drift(self):
        """Keycloak returns ids, timestamps, and defaults it filled in.
        Comparing those would make every pass a write."""
        desired = rendered(fragment_text())["client"]
        live = {
            **desired,
            "id": "some-uuid",
            "nodeReRegistrationTimeout": -1,
            "surrogateAuthRequired": False,
            "authenticationFlowBindingOverrides": {},
        }
        assert translate.client_drift(live, desired) == []

    def test_a_changed_redirect_uri_is_drift(self):
        desired = rendered(fragment_text())["client"]
        live = {**desired, "redirectUris": ["https://elsewhere.example.edu/cb"]}
        assert "redirectUris" in translate.client_drift(live, desired)

    def test_full_scope_turned_on_out_of_band_is_drift(self):
        desired = rendered(fragment_text())["client"]
        live = {**desired, "fullScopeAllowed": True}
        assert "fullScopeAllowed" in translate.client_drift(live, desired)

    def test_a_lost_stamp_is_drift(self):
        desired = rendered(fragment_text())["client"]
        live = {**desired, "attributes": {}}
        drift = translate.client_drift(live, desired)
        assert f"attributes.{translate.STAMP_ATTRIBUTE}" in drift

    def test_merging_attributes_keeps_what_we_do_not_manage(self):
        desired = rendered(fragment_text())["client"]
        live = {"attributes": {"some.operator.note": "keep me"}}
        merged = translate.merged_attributes(live, desired)
        assert merged["some.operator.note"] == "keep me"
        assert merged[translate.STAMP_ATTRIBUTE] == "true"


class TestMapperDrift:
    def test_absent_is_written(self):
        desired = rendered(fragment_text())["protocolMappers"]
        write, delete = translate.mapper_drift([], desired)
        assert [m["name"] for m in write] == ["client-roles"]
        assert delete == []

    def test_identical_is_left_alone(self):
        desired = rendered(fragment_text())["protocolMappers"]
        live = [{**desired[0], "id": "m1"}]
        assert translate.mapper_drift(live, desired) == ([], [])

    def test_a_changed_config_is_updated_in_place(self):
        desired = rendered(fragment_text())["protocolMappers"]
        live = [
            {
                **desired[0],
                "id": "m1",
                "config": {**desired[0]["config"], "claim.name": "tampered"},
            }
        ]
        write, delete = translate.mapper_drift(live, desired)
        assert write[0]["id"] == "m1"
        assert delete == []

    def test_an_undeclared_mapper_on_our_client_is_removed(self):
        """Safe because the client exists only because of this fragment."""
        desired = rendered(fragment_text())["protocolMappers"]
        live = [{**desired[0], "id": "m1"}, {"name": "sneaky-audience", "id": "m2"}]
        write, delete = translate.mapper_drift(live, desired)
        assert write == []
        assert delete == ["sneaky-audience"]
