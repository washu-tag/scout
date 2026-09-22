"""The fragment contract, and specifically what it refuses.

Most of this file is rejections. Each corresponds to something an author could
otherwise do to the realm or to somebody else's tokens.
"""

from __future__ import annotations

import pytest
from conftest import HOSTNAME, TIERS, fragment_text

from scout_keycloak_fragment_reconciler.fragment import (
    API_VERSION,
    RESERVED_CLAIMS,
    FragmentError,
    check_site_rules,
    parse,
)


def parsed(text: str | None = None):
    document = parse(text if text is not None else fragment_text())
    return document.clients[0]


def with_fields(*lines: str) -> str:
    """The fixture fragment plus extra client-level fields."""
    extra = "".join(f"{line}\n" for line in lines)
    return fragment_text().replace("    roles:", f"{extra}    roles:")


def check(spec, *, hostname: str = HOSTNAME, tiers: list[str] | None = None) -> None:
    check_site_rules(spec, hostname=hostname, tiers=TIERS if tiers is None else tiers)


class TestHappyPath:
    def test_hello_scouts_own_fragment_parses(self):
        spec = parsed()
        check(spec)
        assert spec.client_id == "hello"
        assert spec.roles == ["hello-user", "hello-admin"]
        assert spec.grants == {
            "scout-user": ["hello-user"],
            "scout-admin": ["hello-admin"],
        }

    def test_secret_ref_key_defaults_to_what_the_chart_writes(self):
        assert parsed().secret_ref.key == "client-secret"

    def test_defaults_are_the_safe_ones(self):
        spec = parse(
            f"""
            apiVersion: {API_VERSION}
            kind: KeycloakFragment
            clients:
              - clientId: minimal
                displayName: Minimal
                appUrl: https://minimal.{HOSTNAME}
                redirectUris: [https://minimal.{HOSTNAME}/cb]
                secretRef: {{name: minimal-secret}}
            """
        ).clients[0]
        assert spec.role_claim == "groups"
        assert spec.roles == []
        assert spec.grants == {}
        assert spec.description == ""


class TestVersionGate:
    """The forward-compatibility mechanism, since unknown fields are
    rejected rather than ignored."""

    def test_an_unknown_version_is_skipped_whole(self):
        with pytest.raises(FragmentError, match="not supported by this reconciler"):
            parse(fragment_text().replace("v1alpha1", "v2"))

    def test_the_version_is_checked_before_the_body(self):
        """Telling an author their `roles` field is wrong is misleading when
        the real answer is that we do not know their apiVersion."""
        text = fragment_text().replace("v1alpha1", "v2").replace("roles:", "rolez:")
        with pytest.raises(FragmentError, match="apiVersion"):
            parse(text)

    def test_a_wrong_kind_is_rejected(self):
        with pytest.raises(FragmentError, match="kind"):
            parse(fragment_text().replace("KeycloakFragment", "ConfigMap"))


class TestUnknownFieldsAreRejected:
    """Unlike launchpad chips, which warn and ignore.

    A chip that drops a key renders a slightly wrong tile; a fragment that drops
    `publicClient: true` leaves the author believing they shipped one.
    """

    @pytest.mark.parametrize(
        "line",
        [
            "    publicClient: true",
            "    fullScopeAllowed: true",
            "    serviceAccountsEnabled: true",
            "    protocolMappers: []",
            "    defaultClientScopes: [openid]",
            "    authorizationSettings: {}",
            # Both were fields in an earlier draft of v1alpha1, each with one
            # sensible value. Now that Scout decides them, saying either is a
            # capability grab (`pkce: none`) or a no-op that reads as a choice.
            "    pkce: none",
            "    loginFlows: [DIRECT_ACCESS_GRANT]",
        ],
    )
    def test_a_capability_an_app_may_not_grant_itself(self, line):
        with pytest.raises(FragmentError, match="Extra inputs|extra"):
            parse(with_fields(line))

    def test_a_typo_is_not_silently_dropped(self):
        with pytest.raises(FragmentError, match="redirectUrls|Extra"):
            parse(fragment_text().replace("redirectUris:", "redirectUrls:"))


class TestGrantsAreConstrained:
    def test_a_fragment_cannot_grant_a_role_it_does_not_declare(self):
        """Otherwise one app hands another's role to every Scout user."""
        text = fragment_text().replace(
            "scout-user: [hello-user]", "scout-user: [xnat-admin]"
        )
        with pytest.raises(FragmentError, match="does not declare"):
            parse(text)

    def test_a_grant_target_must_be_a_tier_role(self):
        """Otherwise `default-roles-scout` reaches every user in the realm."""
        spec = parsed(fragment_text().replace("scout-user:", "default-roles-scout:"))
        with pytest.raises(FragmentError, match="not a Scout tier role"):
            check(spec)

    def test_the_allowlist_is_what_is_configured(self):
        spec = parsed()
        with pytest.raises(FragmentError, match="not a Scout tier role"):
            check(spec, tiers=["scout-admin"])


class TestRedirectUrisAreConstrained:
    """Unconstrained, this is a token-exfiltration redirect."""

    def test_a_foreign_host_is_rejected(self):
        spec = parsed(
            fragment_text().replace(
                f"https://hello.{HOSTNAME}/auth/callback",
                "https://evil.example.com/steal",
            )
        )
        with pytest.raises(FragmentError, match="outside the site's own domain"):
            check(spec)

    def test_a_lookalike_suffix_is_rejected(self):
        spec = parsed(
            fragment_text().replace(
                f"https://hello.{HOSTNAME}/auth/callback",
                f"https://hello.{HOSTNAME}.evil.com/cb",
            )
        )
        with pytest.raises(FragmentError, match="outside the site's own domain"):
            check(spec)

    def test_a_prefix_that_is_not_a_subdomain_is_rejected(self):
        spec = parsed(
            fragment_text().replace(
                f"https://hello.{HOSTNAME}/auth/callback",
                f"https://not{HOSTNAME}/cb",
            )
        )
        with pytest.raises(FragmentError, match="outside the site's own domain"):
            check(spec)

    def test_a_backslash_in_the_host_is_rejected(self):
        """Python keeps the backslash in the hostname, so the string ends with
        the site's domain; every browser implements the WHATWG URL Standard,
        which reads it as a path separator and resolves the host to what
        precedes it."""
        spec = parsed(
            fragment_text().replace(
                f"https://hello.{HOSTNAME}/auth/callback",
                f"https://evil.com\\.hello.{HOSTNAME}/cb",
            )
        )
        with pytest.raises(FragmentError):
            check(spec)

    def test_a_root_dotted_host_is_the_same_host(self):
        spec = parsed(
            fragment_text().replace(
                f"https://hello.{HOSTNAME}/auth/callback",
                f"https://hello.{HOSTNAME}./cb",
            )
        )
        check(spec)

    def test_the_site_domain_is_matched_case_insensitively(self):
        """A hostname parsed out of a URL is always lowercase, but the
        configured site domain is free-form, so a mixed-case one would put
        every app on the platform outside its own site."""
        check(parsed(), hostname="Scout.Example.EDU")

    def test_an_unconfigured_site_domain_matches_nothing(self):
        """Otherwise the suffix test degenerates to `endswith('.')` and any
        root-dotted FQDN is inside the site's own domain."""
        spec = parsed(fragment_text().replace(f"hello.{HOSTNAME}", "evil.com."))
        with pytest.raises(FragmentError, match="outside the site's own domain"):
            check(spec, hostname="")

    def test_the_bare_site_host_is_allowed(self):
        spec = parsed(
            fragment_text().replace(
                f"https://hello.{HOSTNAME}/auth/callback", f"https://{HOSTNAME}/cb"
            )
        )
        check(spec)

    @pytest.mark.parametrize(
        ("bad", "message"),
        [
            (f"http://hello.{HOSTNAME}/cb", "must use https"),
            (f"https://*.{HOSTNAME}/cb", "wildcard"),
            (f"https://u:p@hello.{HOSTNAME}/cb", "credentials"),
            (f"https://hello.{HOSTNAME}/cb#x", "URL fragment"),
        ],
    )
    def test_shapes_keycloak_would_accept_but_we_do_not(self, bad, message):
        spec = parsed(
            fragment_text().replace(f"https://hello.{HOSTNAME}/auth/callback", bad)
        )
        with pytest.raises(FragmentError, match=message):
            check(spec)

    def test_the_app_url_is_checked_too(self):
        spec = parsed(
            fragment_text().replace(
                f"appUrl: https://hello.{HOSTNAME}", "appUrl: https://evil.example.com"
            )
        )
        with pytest.raises(FragmentError, match="appUrl"):
            check(spec)

    def test_at_least_one_redirect_uri_is_required(self):
        with pytest.raises(FragmentError):
            parse(
                fragment_text().replace(
                    f"      - https://hello.{HOSTNAME}/auth/callback", ""
                )
            )

    def test_duplicate_redirect_uris_are_rejected(self):
        """Keycloak keeps them in a Set, so a repeated URI reads back
        deduplicated and the client looks drifted on every pass."""
        callback = f"      - https://hello.{HOSTNAME}/auth/callback"
        text = fragment_text().replace(callback, f"{callback}\n{callback}")
        with pytest.raises(FragmentError, match="duplicates"):
            parse(text)

    def test_an_app_url_query_string_is_rejected(self):
        """appUrl is a base URL that becomes a web origin and a post-logout
        redirect; a query is meaningless in both. A redirect URI may carry
        one, so this check is appUrl's alone."""
        spec = parsed(
            fragment_text().replace(
                f"appUrl: https://hello.{HOSTNAME}",
                f"appUrl: https://hello.{HOSTNAME}/?next=/x",
            )
        )
        with pytest.raises(FragmentError, match="query string"):
            check(spec)

    def test_a_redirect_uri_query_string_is_allowed(self):
        spec = parsed(
            fragment_text().replace(
                f"https://hello.{HOSTNAME}/auth/callback",
                f"https://hello.{HOSTNAME}/auth/callback?mode=oidc",
            )
        )
        check(spec)

    def test_an_app_url_path_is_allowed(self):
        """An app served under a path is legitimate; only the derived web
        origin drops it."""
        spec = parsed(
            fragment_text().replace(
                f"appUrl: https://hello.{HOSTNAME}",
                f"appUrl: https://hello.{HOSTNAME}/myapp",
            )
        )
        check(spec)
        assert spec.app_origin == f"https://hello.{HOSTNAME}"

    @pytest.mark.parametrize(("port", "in_origin"), [(":443", ""), (":8443", ":8443")])
    def test_the_origin_carries_only_a_non_default_port(self, port, in_origin):
        """A browser's `Origin` omits the scheme's default port, and
        `webOrigins` is compared against that header verbatim."""
        spec = parsed(
            fragment_text().replace(
                f"appUrl: https://hello.{HOSTNAME}",
                f"appUrl: https://hello.{HOSTNAME}{port}",
            )
        )
        check(spec)
        assert spec.app_origin == f"https://hello.{HOSTNAME}{in_origin}"

    def test_a_port_that_is_not_a_number_is_rejected(self):
        """Otherwise there is no origin to derive and the raw appUrl is used
        as a webOrigin instead."""
        spec = parsed(
            fragment_text().replace(
                f"appUrl: https://hello.{HOSTNAME}",
                f"appUrl: https://hello.{HOSTNAME}:abc",
            )
        )
        with pytest.raises(FragmentError, match="port"):
            check(spec)


class TestRoleClaimIsConstrained:
    """A role mapper aimed at a standard claim overwrites what the platform
    reads."""

    @pytest.mark.parametrize(
        "claim", ["sub", "aud", "resource_access", "realm_access", "scope", "email"]
    )
    def test_a_reserved_claim_is_rejected(self, claim):
        with pytest.raises(FragmentError, match="reserved claim"):
            parse(with_fields(f"    roleClaim: {claim}"))

    @pytest.mark.parametrize(
        "claim", ["realm_access.roles", "resource_access.trino.roles"]
    )
    def test_a_path_into_a_reserved_claim_is_rejected(self, claim):
        """Keycloak splits a mapper's `claim.name` on dots and builds a nested
        object, so a dotted name lands inside the claim it starts with."""
        with pytest.raises(FragmentError, match="reserved claim"):
            parse(with_fields(f"    roleClaim: {claim}"))

    @pytest.mark.parametrize("claim", ["groups", "hello-roles"])
    def test_a_name_of_its_own_is_accepted(self, claim):
        assert parsed(with_fields(f"    roleClaim: {claim}")).role_claim == claim

    def test_groups_is_the_default_and_needs_no_field(self):
        assert "groups" not in RESERVED_CLAIMS
        assert "roleClaim" not in fragment_text()
        assert parsed().role_claim == "groups"

    def test_a_service_that_reads_roles_elsewhere_may_override_it(self):
        """The one reason the field still exists: temporal reads
        `permissions`, minio reads `policy`."""
        assert parsed(with_fields("    roleClaim: permissions")).role_claim == (
            "permissions"
        )


class TestIdentifiersAreConstrained:
    @pytest.mark.parametrize(
        "client_id",
        [
            "has space",
            "../escape",
            "with/slash",
            "$(env:oauth2_proxy)",
            "",
            "a" * 64,
        ],
    )
    def test_a_client_id_that_would_break_a_url_or_a_claim(self, client_id):
        with pytest.raises(FragmentError):
            parse(
                fragment_text().replace("clientId: hello", f"clientId: {client_id!r}")
            )

    @pytest.mark.parametrize(
        "role",
        [
            "../../../roles/scout-admin",
            "with/slash",
            "has space",
            "",
            "a" * 64,
        ],
    )
    def test_a_role_that_would_break_a_url(self, role):
        """A role name is interpolated into an admin-API path.

        `../../../roles/scout-admin` is the one that matters: the reconciler
        deletes roles an author drops from `roles`, and those dots resolve
        before the request leaves, turning the delete into one aimed at a base
        realm tier role -- which this service must never be able to touch.
        """
        with pytest.raises(FragmentError, match="should match pattern"):
            parse(fragment_text().replace("      - hello-user", f"      - {role!r}"))

    def test_duplicate_roles_are_rejected(self):
        text = fragment_text().replace("      - hello-admin", "      - hello-user")
        with pytest.raises(FragmentError, match="duplicates"):
            parse(text)

    def test_two_clients_in_one_document_cannot_share_a_client_id(self):
        text = fragment_text("hello") + fragment_text("hello").split("clients:")[1]
        with pytest.raises(FragmentError, match="share a clientId"):
            parse(text)


class TestMalformedDocuments:
    def test_not_yaml(self):
        with pytest.raises(FragmentError, match="not valid YAML"):
            parse("{{{ nope")

    def test_not_a_mapping(self):
        with pytest.raises(FragmentError, match="must be a YAML mapping"):
            parse("- just\n- a\n- list")

    def test_no_clients(self):
        with pytest.raises(FragmentError):
            parse(f"apiVersion: {API_VERSION}\nkind: KeycloakFragment\nclients: []")

    def test_deeply_nested_yaml_does_not_escape_the_contract(self):
        """A document nested past the interpreter's recursion limit is one
        author's mistake, not an abort of the whole pass."""
        depth = 60000
        text = (
            f"apiVersion: {API_VERSION}\nkind: KeycloakFragment\n"
            f"clients: {'[' * depth}{']' * depth}\n"
        )
        with pytest.raises(FragmentError):
            parse(text)
