"""JWT validation tests.

We generate an RSA keypair at import time, build a fake JWKS with the
public half, inject it into the cache, and mint test JWTs with the
private half. No external Keycloak needed.
"""

from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jose import jwt

from scout_report_viewer import jwks
from scout_report_viewer.app import create_app
from scout_report_viewer.config import settings


_KID = "test-key-1"


@pytest.fixture(scope="module")
def keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_numbers = priv.public_key().public_numbers()
    # JWK n/e encoding per RFC 7518.
    import base64

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
    monkeypatch.setattr(settings, "keycloak_jwks_url", "http://test/jwks")
    monkeypatch.setattr(settings, "keycloak_issuer", "")
    yield


def _mint(priv_pem: bytes, **overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": "alice-keycloak-uuid",
        "preferred_username": "alice",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return jwt.encode(claims, priv_pem, algorithm="RS256", headers={"kid": _KID})


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
        headers={"kid": _KID},
    )
    with TestClient(create_app()) as client:
        r = client.post(
            "/api/searches",
            json={"sql": "SELECT 1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401


def test_oauth2_proxy_header_still_works(keypair):
    # Header path is the fallback when no bearer is present. JWT setup
    # shouldn't break it.
    with TestClient(create_app()) as client:
        r = client.post(
            "/api/searches",
            json={"sql": "SELECT 1"},
            headers={"X-Auth-Request-Preferred-Username": "alice"},
        )
        assert r.status_code != 401


def test_invalid_bearer_does_NOT_fall_through_to_header(keypair):
    """If a caller sends both a bad bearer AND the header, we should 401
    on the bearer — silently falling back would mask token-expiry bugs."""
    priv, _ = keypair
    expired = _mint(priv, exp=int(time.time()) - 60)
    with TestClient(create_app()) as client:
        r = client.post(
            "/api/searches",
            json={"sql": "SELECT 1"},
            headers={
                "Authorization": f"Bearer {expired}",
                "X-Auth-Request-Preferred-Username": "alice",
            },
        )
        assert r.status_code == 401
