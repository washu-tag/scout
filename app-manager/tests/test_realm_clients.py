"""Every client in the real Scout realm, expressed as a fragment.

This is the acceptance test for the vocabulary. If a client Scout actually runs
cannot be written as a fragment, the vocabulary is not finished -- and the v0
schema failed all twelve, which is why they are enumerated here rather than
sampled.

Expectations are the values read off the live dev03 realm on 2026-08-27.
"""

import textwrap

import pytest
import yaml
from conftest import write_fragment

from scout_app_manager.compose import Site
from scout_app_manager.compose import compose as compose_realm
from scout_app_manager.load import scan
from scout_app_manager.schema import Fragment

DOMAIN = "dev03.tag.rcif.io"
SIGNOUT = f"https://auth.{DOMAIN}/oauth2/sign_out"


def fragment(body: str) -> str:
    return (
        textwrap.dedent(
            """
        apiVersion: keycloak.scout.xnat.org/v1alpha1
        kind: KeycloakFragment
        """
        ).lstrip()
        + textwrap.dedent(body).lstrip()
    )


# (id, fragment, expected redirectUris beyond signout, expected extras)
REALM_CLIENTS = [
    (
        "oauth2-proxy",
        fragment(
            """
            clients:
              - clientId: oauth2-proxy
                secretRef:
                  name: oauth2-proxy-keycloak-client
                displayName: OAuth2 Proxy
                loginFlows: [STANDARD]
                redirectUris:
                  - https://auth.${domain}/oauth2/callback
                  - https://superset.${domain}/oauth2/idpresponse
                roles: [oauth2-proxy-user]
                grants:
                  scout-user: [oauth2-proxy-user]
                  scout-admin: [oauth2-proxy-user]
            """
        ),
        [
            f"https://auth.{DOMAIN}/oauth2/callback",
            f"https://superset.{DOMAIN}/oauth2/idpresponse",
        ],
        {"webOrigins": [f"https://auth.{DOMAIN}", f"https://superset.{DOMAIN}"]},
    ),
    (
        "jupyterhub",
        fragment(
            """
            clients:
              - clientId: jupyterhub
                secretRef:
                  name: jupyterhub-keycloak-client
                loginFlows: [STANDARD]
                redirectUris:
                  - https://jupyter.${domain}/hub/oauth_callback
                roles: [jupyterhub-admin, jupyterhub-user]
                grants:
                  scout-admin: [jupyterhub-admin]
                  scout-user: [jupyterhub-user]
            """
        ),
        [f"https://jupyter.{DOMAIN}/hub/oauth_callback"],
        {},
    ),
    (
        "grafana",
        fragment(
            """
            clients:
              - clientId: grafana
                secretRef:
                  name: grafana-keycloak-client
                loginFlows: [STANDARD]
                redirectUris:
                  - https://grafana.${domain}/login/generic_oauth
                roles: [grafana-admin, grafana-editor, grafana-viewer]
                grants:
                  scout-admin: [grafana-admin]
            """
        ),
        [f"https://grafana.{DOMAIN}/login/generic_oauth"],
        {},
    ),
    (
        "temporal",
        fragment(
            """
            clients:
              - clientId: temporal
                secretRef:
                  name: temporal-keycloak-client
                loginFlows: [STANDARD]
                redirectUris:
                  - https://temporal.${domain}/auth/sso/callback
                roleClaim: permissions
                accessTokenLifespan: 8h
                sessionLifespan: 8h
                roles: ['temporal-system:admin', 'default:admin']
                grants:
                  scout-admin: ['temporal-system:admin', 'default:admin']
            """
        ),
        [f"https://temporal.{DOMAIN}/auth/sso/callback"],
        {
            "claim": "permissions",
            "attributes": {
                "access.token.lifespan": "28800",
                "client.session.idle.timeout": "28800",
                "client.session.max.lifespan": "28800",
                "client.offline.session.idle.timeout": "28800",
            },
        },
    ),
    (
        "superset",
        fragment(
            """
            clients:
              - clientId: superset
                secretRef:
                  name: superset-keycloak-client
                loginFlows: [STANDARD]
                redirectUris:
                  - https://superset.${domain}/oauth-authorized/keycloak
                roles: [superset_admin, superset_alpha, superset_gamma, superset_sql_lab]
                grants:
                  scout-admin: [superset_admin]
                  scout-user: [superset_alpha, superset_sql_lab]
            """
        ),
        [f"https://superset.{DOMAIN}/oauth-authorized/keycloak"],
        {},
    ),
    (
        "minio",
        fragment(
            """
            clients:
              - clientId: minio
                secretRef:
                  name: minio-keycloak-client
                loginFlows: [STANDARD]
                redirectUris:
                  - https://minio.${domain}/oauth_callback
                roleClaim: policy
                accessTokenLifespan: 8h
                sessionLifespan: 8h
                roles: [consoleAdmin]
                grants:
                  scout-admin: [consoleAdmin]
            """
        ),
        [f"https://minio.{DOMAIN}/oauth_callback"],
        {"claim": "policy"},
    ),
    (
        "launchpad",
        fragment(
            """
            clients:
              - clientId: launchpad
                secretRef:
                  name: launchpad-keycloak-client
                loginFlows: [STANDARD]
                appUrl: https://${domain}
                redirectUris:
                  - https://${domain}/api/auth/callback/keycloak
                roles: [launchpad-admin, launchpad-user]
                grants:
                  scout-admin: [launchpad-admin]
                  scout-user: [launchpad-user]
            """
        ),
        [f"https://{DOMAIN}/api/auth/callback/keycloak"],
        {"webOrigins": [f"https://{DOMAIN}"]},
    ),
    (
        "open-webui",
        fragment(
            """
            clients:
              - clientId: open-webui
                secretRef:
                  name: open-webui-keycloak-client
                loginFlows: [STANDARD]
                redirectUris:
                  - https://chat.${domain}/oauth/oidc/callback
                roles: [open-webui-admin, open-webui-user]
                grants:
                  scout-admin: [open-webui-admin]
                  scout-user: [open-webui-user]
            """
        ),
        [f"https://chat.{DOMAIN}/oauth/oidc/callback"],
        {},
    ),
    (
        "superset_svc",
        fragment(
            """
            clients:
              - clientId: superset_svc
                secretRef:
                  name: superset-svc-keycloak-client
                description: Service principal for Superset impersonating via X-Trino-User
                loginFlows: [SERVICE_ACCOUNT]
                accessTokenLifespan: 4h
            """
        ),
        [],
        {"service_account": True, "attributes": {"access.token.lifespan": "14400"}},
    ),
    (
        "voila_svc",
        fragment(
            """
            clients:
              - clientId: voila_svc
                secretRef:
                  name: voila-svc-keycloak-client
                loginFlows: [SERVICE_ACCOUNT]
                accessTokenLifespan: 4h
            """
        ),
        [],
        {"service_account": True},
    ),
    (
        "report_viewer_svc",
        fragment(
            """
            clients:
              - clientId: report_viewer_svc
                secretRef:
                  name: report-viewer-svc-keycloak-client
                loginFlows: [SERVICE_ACCOUNT]
                accessTokenLifespan: 4h
            """
        ),
        [],
        {"service_account": True},
    ),
    (
        "xnat",
        fragment(
            """
            clients:
              - clientId: xnat
                secretRef:
                  name: xnat-keycloak-client
                loginFlows: [STANDARD]
                redirectUris:
                  - https://xnat.${domain}/openid-login
                pkce: required
                roles: [xnat-access]
                grants:
                  scout-user: [xnat-access]
            """
        ),
        [f"https://xnat.{DOMAIN}/openid-login"],
        {"pkce": "S256"},
    ),
]


@pytest.mark.parametrize(
    "name,body,uris,extras", REALM_CLIENTS, ids=[c[0] for c in REALM_CLIENTS]
)
def test_every_realm_client_is_expressible(name, body, uris, extras):
    """v0 expressed none of these. Each one is a line item in the review doc."""
    Fragment.model_validate(yaml.safe_load(body))


@pytest.mark.parametrize(
    "name,body,uris,extras", REALM_CLIENTS, ids=[c[0] for c in REALM_CLIENTS]
)
def test_composed_client_matches_the_live_realm(
    name, body, uris, extras, base_realm, tmp_path
):
    write_fragment(tmp_path, "scout-test", name.replace("_", "-"), body)
    result = compose_realm(
        {"realm": "scout", "clients": [], "groups": base_realm["groups"]},
        scan(tmp_path),
        Site(domain=DOMAIN),
    )
    assert not result.rejected, result.rejected
    client = next(c for c in result.realm["clients"] if c["clientId"] == name)

    if extras.get("service_account"):
        assert client["serviceAccountsEnabled"] is True
        assert client["standardFlowEnabled"] is False
        assert "redirectUris" not in client
    else:
        # The platform signout URI is injected, never declared: eight of nine
        # real interactive clients carry it and none should hardcode it.
        assert client["redirectUris"] == uris + [SIGNOUT]
        assert client["standardFlowEnabled"] is True

    if "webOrigins" in extras:
        assert client["webOrigins"] == extras["webOrigins"]
    if "claim" in extras:
        assert client["protocolMappers"][0]["config"]["claim.name"] == extras["claim"]
    for key, value in extras.get("attributes", {}).items():
        assert client["attributes"][key] == value

    assert client["attributes"].get("pkce.code.challenge.method") == extras.get("pkce")
    assert client["fullScopeAllowed"] is False
    assert client["publicClient"] is False
    assert client["directAccessGrantsEnabled"] is False
    assert client["implicitFlowEnabled"] is False


def test_grants_reproduce_the_real_group_role_maps(base_realm, tmp_path):
    """The whole of scout-user and scout-admin, assembled from fragments."""
    for name, body, _, _ in REALM_CLIENTS:
        write_fragment(tmp_path, "scout-test", name.replace("_", "-"), body)
    result = compose_realm(
        {
            "realm": "scout",
            "clients": [],
            "groups": [
                {"name": "scout-admin", "clientRoles": {}},
                {"name": "scout-user", "clientRoles": {}},
            ],
        },
        scan(tmp_path),
        Site(domain=DOMAIN),
    )
    assert not result.rejected, result.rejected
    groups = {g["name"]: g["clientRoles"] for g in result.realm["groups"]}

    # Read off the live realm, minus realm-management (platform-owned, and a
    # fragment has no syntax for it) and xnat's grant which is feature-gated.
    assert groups["scout-user"] == {
        "oauth2-proxy": ["oauth2-proxy-user"],
        "jupyterhub": ["jupyterhub-user"],
        "launchpad": ["launchpad-user"],
        "superset": ["superset_alpha", "superset_sql_lab"],
        "open-webui": ["open-webui-user"],
        "xnat": ["xnat-access"],
    }
    assert groups["scout-admin"] == {
        "oauth2-proxy": ["oauth2-proxy-user"],
        "jupyterhub": ["jupyterhub-admin"],
        "launchpad": ["launchpad-admin"],
        "superset": ["superset_admin"],
        "grafana": ["grafana-admin"],
        "temporal": ["temporal-system:admin", "default:admin"],
        "open-webui": ["open-webui-admin"],
        "minio": ["consoleAdmin"],
    }


def test_token_exchange_client_from_the_branch():
    """xnat-downloader, the pluggable-app POC's hardest case."""
    Fragment.model_validate(
        yaml.safe_load(
            fragment(
                """
                clients:
                  - clientId: xnat-downloader
                    secretRef:
                      name: xnat-downloader-keycloak-client
                    loginFlows: [STANDARD, TOKEN_EXCHANGE]
                    redirectUris:
                      - https://downloader.${domain}/auth/callback
                """
            )
        )
    )
