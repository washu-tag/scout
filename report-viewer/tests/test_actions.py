"""Tests for issue #739's action-descriptor contract and role filtering.

Most of this is pure-function coverage over `actions.py` (no Postgres/JWKS
needed). One end-to-end test drives the real `/api/searches/{id}/actions`
endpoint through a Bearer JWT carrying a `resource_access` role claim, to
prove auth.py's role extraction actually reaches the route - see
test_jwt_auth.py for the JWKS-mocking pattern this borrows.
"""

from __future__ import annotations

import base64
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from pydantic import ValidationError

from scout_report_viewer import jwks
from scout_report_viewer.actions import ActionDescriptor, _is_safe_action_url, list_actions
from scout_report_viewer.config import settings


# --- Pure-function tests: no fixtures required ---------------------------


def test_list_actions_excludes_role_gated_action_without_role():
    ids = {a.id for a in list_actions(frozenset())}
    assert "admin-diagnostics-poc" not in ids
    assert "docs-link" in ids
    assert "download-csv" in ids


def test_list_actions_includes_role_gated_action_with_role():
    ids = {a.id for a in list_actions(frozenset({"report-viewer-admin"}))}
    assert "admin-diagnostics-poc" in ids


def test_list_actions_sorted_by_weight():
    actions = list_actions(frozenset({"report-viewer-admin"}))
    weights = [a.weight for a in actions]
    assert weights == sorted(weights)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.org/x", True),
        ("http://example.org/x", True),
        ("javascript:alert(1)", False),
        ("data:text/html,<script>alert(1)</script>", False),
        ("not-a-url", False),
        ("", False),
    ],
)
def test_is_safe_action_url(url, expected):
    assert _is_safe_action_url(url) is expected


def test_open_url_action_requires_safe_url():
    with pytest.raises(ValidationError):
        ActionDescriptor(id="x", title="X", action_type="open-url", url="javascript:alert(1)")
    with pytest.raises(ValidationError):
        ActionDescriptor(id="x", title="X", action_type="open-url", url=None)


def test_client_action_requires_handler():
    with pytest.raises(ValidationError):
        ActionDescriptor(id="x", title="X", action_type="client", client_handler=None)


# --- End-to-end: role claim -> auth.py -> route ---------------------------

_KID = "test-actions-key"
_ISSUER = "http://test/realms/scout"


@pytest.fixture(scope="module")
def keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_numbers = priv.public_key().public_numbers()

    def _b64(n: int) -> str:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    jwk = {
        "kty": "RSA",
        "kid": _KID,
        "alg": "RS256",
        "use": "sig",
        "n": _b64(pub_numbers.n),
        "e": _b64(pub_numbers.e),
    }
    return priv_pem, jwk


@pytest.fixture(autouse=True)
def install_test_jwks(keypair, monkeypatch):
    _, jwk = keypair

    class _StaticCache:
        def get_key(self, kid):
            return jwk if kid == _KID else None

    monkeypatch.setattr(jwks, "get_default", lambda url: _StaticCache())
    monkeypatch.setattr(settings, "oidc_jwks_url", "http://test/jwks")
    monkeypatch.setattr(settings, "oidc_issuer", _ISSUER)
    yield


def _mint(priv_pem: bytes, roles: list[str] | None = None) -> str:
    now = int(time.time())
    claims = {
        "sub": "carol-keycloak-uuid",
        "preferred_username": "carol",
        "iss": _ISSUER,
        "aud": settings.oidc_audience,
        "iat": now,
        "exp": now + 300,
    }
    if roles is not None:
        claims["resource_access"] = {settings.oidc_roles_client_id: {"roles": roles}}
    return jwt.encode(claims, priv_pem, algorithm="RS256", headers={"kid": _KID})


def _create_search(client, token: str, fake_trino) -> str:
    fake_trino(
        ["primary_report_identifier", "accession_number"],
        [{"primary_report_identifier": "s3://x/1", "accession_number": "ACC1"}],
    )
    fake_trino(["n"], [{"n": 1}])
    r = client.post(
        "/api/searches",
        json={"sql": "SELECT primary_report_identifier, accession_number FROM reports_latest"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_actions_endpoint_hides_admin_action_without_role(client, keypair, fake_trino):
    priv, _ = keypair
    token = _mint(priv, roles=[])
    search_id = _create_search(client, token, fake_trino)

    r = client.get(f"/api/searches/{search_id}/actions", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    ids = {a["id"] for a in r.json()}
    assert "admin-diagnostics-poc" not in ids


def test_actions_endpoint_shows_admin_action_with_role(client, keypair, fake_trino):
    priv, _ = keypair
    token = _mint(priv, roles=["report-viewer-admin"])
    search_id = _create_search(client, token, fake_trino)

    r = client.get(f"/api/searches/{search_id}/actions", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    ids = {a["id"] for a in r.json()}
    assert "admin-diagnostics-poc" in ids


def test_actions_endpoint_404s_for_someone_elses_search(client, keypair, fake_trino):
    priv, _ = keypair
    owner_token = _mint(priv)
    search_id = _create_search(client, owner_token, fake_trino)

    other_token = _mint(priv, roles=["report-viewer-admin"])
    r = client.get(
        f"/api/searches/{search_id}/actions", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert r.status_code == 404
