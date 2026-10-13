"""JWT validation tests.

The RSA keypair, fake JWKS, and `mint`/`mint_token` helpers are shared
fixtures from conftest.py - this file just uses them. No external Keycloak
needed.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from scout_report_viewer.app import create_app
from scout_report_viewer.auth import _validate_jwt
from scout_report_viewer.config import settings

from .conftest import TEST_ISSUER, TEST_KID
from .conftest import mint_token as _mint


def test_unauthenticated_create_returns_401():
    with TestClient(create_app()) as client:
        r = client.post("/api/searches", json={"sql": "SELECT 1"})
        assert r.status_code == 401


def test_valid_bearer_authenticates_via_sub_claim(keypair):
    priv, _ = keypair
    token = _mint(priv)
    with TestClient(create_app()) as client:
        r = client.post(
            "/api/searches",
            json={"sql": "SELECT 1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        # 401 would mean auth failed. Anything else (400 for empty SQL
        # result, 502 for Trino, etc.) means the JWT path authenticated.
        assert r.status_code != 401, r.text


def test_expired_bearer_returns_401(keypair):
    priv, _ = keypair
    token = _mint(priv, exp=int(time.time()) - 60)
    with TestClient(create_app()) as client:
        r = client.post(
            "/api/searches",
            json={"sql": "SELECT 1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401
        assert "bearer" in r.text.lower()


def test_unknown_kid_returns_401(keypair):
    priv, _ = keypair
    # Mint a JWT with a kid the cache doesn't know.
    token = jwt.encode(
        {"sub": "alice", "exp": int(time.time()) + 300},
        priv,
        algorithm="RS256",
        headers={"kid": "wrong-kid"},
    )
    with TestClient(create_app()) as client:
        r = client.post(
            "/api/searches",
            json={"sql": "SELECT 1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401


def test_bearer_without_sub_returns_401(keypair):
    priv, _ = keypair
    token = jwt.encode(
        {"iat": int(time.time()), "exp": int(time.time()) + 300},
        priv,
        algorithm="RS256",
        headers={"kid": TEST_KID},
    )
    with TestClient(create_app()) as client:
        r = client.post(
            "/api/searches",
            json={"sql": "SELECT 1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401


def test_invalid_bearer_returns_401_even_with_no_other_identity(keypair):
    """Bearer is the only auth path - an invalid one must 401, not fall
    through to anything else."""
    priv, _ = keypair
    expired = _mint(priv, exp=int(time.time()) - 60)
    with TestClient(create_app()) as client:
        r = client.post(
            "/api/searches",
            json={"sql": "SELECT 1"},
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert r.status_code == 401


def test_bearer_without_aud_returns_401(keypair):
    """python-jose accepts tokens missing `aud` when `audience=` is passed;
    the explicit post-decode guard must catch this."""
    priv, _ = keypair
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "alice",
            "preferred_username": "alice",
            "iss": TEST_ISSUER,
            "iat": now,
            "exp": now + 300,
        },
        priv,
        algorithm="RS256",
        headers={"kid": TEST_KID},
    )
    with TestClient(create_app()) as client:
        r = client.post(
            "/api/searches",
            json={"sql": "SELECT 1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401


def test_wrong_issuer_returns_401(keypair):
    priv, _ = keypair
    token = _mint(priv, iss="http://attacker/realms/evil")
    with TestClient(create_app()) as client:
        r = client.post(
            "/api/searches",
            json={"sql": "SELECT 1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401


def test_hs256_token_is_rejected_by_allowlist():
    """The allowlist is enforced against the token header's `alg`
    independent of the JWK. Sending an HS256 token with any secret must
    401 - if this ever passes we've regressed to accepting whatever alg
    the caller declared."""
    now = int(time.time())
    forged = jwt.encode(
        {"sub": "attacker", "iss": TEST_ISSUER, "iat": now, "exp": now + 300},
        "any-symmetric-secret",
        algorithm="HS256",
        headers={"kid": TEST_KID},
    )
    with TestClient(create_app()) as client:
        r = client.post(
            "/api/searches",
            json={"sql": "SELECT 1"},
            headers={"Authorization": f"Bearer {forged}"},
        )
        assert r.status_code == 401


_FWD = "X-Amzn-Oidc-Accesstoken"


def _post_with(headers: dict[str, str]):
    with TestClient(create_app()) as client:
        return client.post("/api/searches", json={"sql": "SELECT 1"}, headers=headers)


def test_forwarded_token_authenticates_when_configured(keypair, monkeypatch):
    priv, _ = keypair
    monkeypatch.setattr(settings, "forwarded_token_header", _FWD)
    r = _post_with({_FWD: _mint(priv)})
    assert r.status_code != 401, r.text


def test_forwarded_token_ignored_when_unconfigured(keypair, monkeypatch):
    priv, _ = keypair
    monkeypatch.setattr(settings, "forwarded_token_header", "")
    assert _post_with({_FWD: _mint(priv)}).status_code == 401


def test_invalid_forwarded_token_returns_401(keypair, monkeypatch):
    priv, _ = keypair
    monkeypatch.setattr(settings, "forwarded_token_header", _FWD)
    r = _post_with({_FWD: _mint(priv, aud="oauth2-proxy")})
    assert r.status_code == 401
    assert "bearer" in r.text.lower()


def test_bearer_takes_precedence_over_forwarded_token(keypair, monkeypatch):
    priv, _ = keypair
    monkeypatch.setattr(settings, "forwarded_token_header", _FWD)
    r = _post_with({"Authorization": f"Bearer {_mint(priv)}", _FWD: "not-a-jwt"})
    assert r.status_code != 401, r.text


def test_bearer_with_resource_access_roles_populates_user_roles(keypair):
    priv, _ = keypair
    token = _mint(
        priv,
        resource_access={
            "report-viewer": {"roles": ["report-viewer-admin", "report-viewer-user"]}
        },
    )
    user = _validate_jwt(token)
    assert user is not None
    assert user.roles == frozenset({"report-viewer-admin", "report-viewer-user"})


def test_bearer_without_resource_access_claim_yields_empty_roles(keypair):
    priv, _ = keypair
    user = _validate_jwt(_mint(priv))
    assert user is not None
    assert user.roles == frozenset()


def test_bearer_with_other_clients_resource_access_yields_empty_roles(keypair):
    """resource_access is keyed per-client - a role under a different
    client's entry must not leak into report-viewer's roles."""
    priv, _ = keypair
    token = _mint(
        priv, resource_access={"some-other-client": {"roles": ["some-other-role"]}}
    )
    user = _validate_jwt(token)
    assert user is not None
    assert user.roles == frozenset()


def test_settings_rejects_jwks_url_without_issuer(monkeypatch):
    """Startup guard: if REPORT_VIEWER_OIDC_JWKS_URL is configured but
    REPORT_VIEWER_OIDC_ISSUER is empty, Settings() must refuse to
    construct - passing an empty issuer to python-jose disables `iss`
    validation silently."""
    from pydantic import ValidationError

    from scout_report_viewer.config import Settings

    monkeypatch.setenv("REPORT_VIEWER_EXTERNAL_URL", "http://testserver")
    monkeypatch.setenv("REPORT_VIEWER_OIDC_JWKS_URL", "http://kc/realms/scout/jwks")
    monkeypatch.delenv("REPORT_VIEWER_OIDC_ISSUER", raising=False)
    with pytest.raises(ValidationError):
        Settings()
