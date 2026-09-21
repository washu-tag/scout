"""Tests for issue #739's action-descriptor contract and group filtering.

Most of this is pure-function coverage over `actions.py` (no Postgres/JWKS
needed). The end-to-end tests drive the real `/api/searches/{id}/actions`
endpoint through both real auth paths: the oauth2-proxy header path (Path
2, `X-Auth-Request-Groups`) - the only path that can actually gate what
the SPA renders, since it's the only path the SPA's own requests take -
and the Bearer JWT path (Path 1), which carries no group claim at all, to
lock in that a group-gated action never becomes visible through it. See
test_jwt_auth.py for the JWKS-mocking pattern the Bearer-path tests
borrow.
"""

from __future__ import annotations

import base64
import os
import time

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import JWTError, jwt
from pydantic import ValidationError

from scout_report_viewer import actions, jwks
from scout_report_viewer.actions import (
    ActionDescriptor,
    _is_safe_action_url,
    _load_catalog_from_file,
    list_actions,
    load_invoke_token,
    mint_user_assertion,
)
from scout_report_viewer.config import settings


# --- Pure-function tests: no fixtures required ---------------------------


def test_default_catalog_matches_shipped_toolbar():
    """The no-ConfigMap-mounted floor must be exactly today's real
    toolbar (explain-search, download-csv) - see actions.py's
    _DEFAULT_CATALOG docstring. Demo/example entries belong in a test's
    own monkeypatched catalog, not this fallback."""
    ids = {a.id for a in list_actions(frozenset())}
    assert ids == {"explain-search", "download-csv"}


_ADMIN_GROUP = "scout-admin"


@pytest.fixture
def catalog_with_admin_action(monkeypatch):
    """A temporary catalog carrying a group-gated entry, for tests that
    need to exercise gating without it living in the shipped default."""
    demo_catalog = [
        *actions._DEFAULT_CATALOG,
        ActionDescriptor(
            id="admin-only-demo",
            title="Admin Only (test)",
            action_type="open-url",
            url="https://example.org/admin",
            required_group=_ADMIN_GROUP,
        ),
    ]
    monkeypatch.setattr(actions, "_CATALOG", demo_catalog)
    return demo_catalog


def test_list_actions_excludes_group_gated_action_without_group(
    catalog_with_admin_action,
):
    ids = {a.id for a in list_actions(frozenset())}
    assert "admin-only-demo" not in ids
    assert "download-csv" in ids


def test_list_actions_includes_group_gated_action_with_group(catalog_with_admin_action):
    ids = {a.id for a in list_actions(frozenset({_ADMIN_GROUP}))}
    assert "admin-only-demo" in ids


def test_list_actions_sorted_by_weight(catalog_with_admin_action):
    result = list_actions(frozenset({_ADMIN_GROUP}))
    weights = [a.weight for a in result]
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
        ActionDescriptor(
            id="x", title="X", action_type="open-url", url="javascript:alert(1)"
        )
    with pytest.raises(ValidationError):
        ActionDescriptor(id="x", title="X", action_type="open-url", url=None)


def test_client_action_requires_handler():
    with pytest.raises(ValidationError):
        ActionDescriptor(id="x", title="X", action_type="client", client_handler=None)


def test_backend_call_action_requires_safe_endpoint_url():
    with pytest.raises(ValidationError):
        ActionDescriptor(
            id="x", title="X", action_type="backend-call", endpoint_url=None
        )
    with pytest.raises(ValidationError):
        ActionDescriptor(
            id="x",
            title="X",
            action_type="backend-call",
            endpoint_url="javascript:alert(1)",
        )


def test_backend_call_action_has_no_invoke_token_field():
    """invoke tokens are never part of the descriptor at all - see
    load_invoke_token() - so there is structurally nothing here that could
    round-trip to the browser."""
    d = ActionDescriptor(
        id="x",
        title="X",
        action_type="backend-call",
        endpoint_url="https://example.org/invoke",
    )
    assert "invoke_token" not in d.model_dump()
    assert "invoke_token" not in d.model_dump_json()
    assert not hasattr(d, "invoke_token")


def test_load_invoke_token_missing_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "action_tokens_path", str(tmp_path))
    assert load_invoke_token("does-not-exist") is None


def test_load_invoke_token_reads_and_strips_file_contents(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "action_tokens_path", str(tmp_path))
    (tmp_path / "explore-xnat").write_text("super-secret\n")
    assert load_invoke_token("explore-xnat") == "super-secret"


def test_mint_user_assertion_missing_key_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "action_tokens_path", str(tmp_path))
    assert mint_user_assertion("explore-xnat", "carol", frozenset(), "s1") is None


def test_mint_user_assertion_signs_expected_claims(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "action_tokens_path", str(tmp_path))
    (tmp_path / "explore-xnat.assertion-key").write_text("assertion-secret")

    token = mint_user_assertion(
        "explore-xnat", "carol", frozenset({"scout-admin", "scout-user"}), "s1"
    )
    assert token is not None
    claims = jwt.decode(token, "assertion-secret", algorithms=["HS256"])
    assert claims["sub"] == "carol"
    assert claims["groups"] == ["scout-admin", "scout-user"]
    assert claims["search_id"] == "s1"
    assert claims["action_id"] == "explore-xnat"
    assert claims["exp"] - claims["iat"] == 60


def test_mint_user_assertion_uses_separate_key_from_invoke_token(tmp_path, monkeypatch):
    """A leaked invoke_token must not be usable to forge an assertion -
    the two live under different filenames and are independent values."""
    monkeypatch.setattr(settings, "action_tokens_path", str(tmp_path))
    (tmp_path / "explore-xnat").write_text("invoke-secret")
    (tmp_path / "explore-xnat.assertion-key").write_text("assertion-secret")

    token = mint_user_assertion("explore-xnat", "carol", frozenset(), "s1")
    assert token is not None
    with pytest.raises(JWTError):
        jwt.decode(token, "invoke-secret", algorithms=["HS256"])


# --- _load_catalog_from_file: the site-admin-facing actions.custom path ---


def test_load_catalog_missing_file_returns_none(tmp_path):
    assert _load_catalog_from_file(str(tmp_path / "nope.yaml")) is None


def test_load_catalog_empty_file_is_a_valid_empty_list(tmp_path):
    """Distinguishes "every action disabled" from "no file mounted" -
    the caller must NOT treat this the same as None (see actions.py's
    _CATALOG assignment comment)."""
    f = tmp_path / "catalog.yaml"
    f.write_text("")
    assert _load_catalog_from_file(str(f)) == []


def test_load_catalog_skips_invalid_entry_keeps_rest(tmp_path):
    f = tmp_path / "catalog.yaml"
    f.write_text(
        "- id: bad\n"
        "  title: Bad\n"
        "  action_type: open-url\n"
        "  url: javascript:alert(1)\n"
        "- id: good\n"
        "  title: Good\n"
        "  action_type: open-url\n"
        "  url: https://example.org\n"
    )
    result = _load_catalog_from_file(str(f))
    ids = {a.id for a in result}
    assert ids == {"good"}


def test_load_catalog_rejects_later_duplicate_id(tmp_path):
    """Matches ADR 0034's chip rule: duplicate ids reject the later entry -
    e.g. an actions.custom id colliding with a built-in id."""
    f = tmp_path / "catalog.yaml"
    f.write_text(
        "- id: dup\n"
        "  title: First\n"
        "  action_type: open-url\n"
        "  url: https://example.org/first\n"
        "- id: dup\n"
        "  title: Second\n"
        "  action_type: open-url\n"
        "  url: https://example.org/second\n"
    )
    result = _load_catalog_from_file(str(f))
    assert len(result) == 1
    assert result[0].title == "First"


def test_load_catalog_malformed_yaml_returns_none(tmp_path):
    f = tmp_path / "catalog.yaml"
    f.write_text("[1, 2")  # unclosed flow sequence
    assert _load_catalog_from_file(str(f)) is None


def test_load_catalog_non_list_yaml_returns_none(tmp_path):
    f = tmp_path / "catalog.yaml"
    f.write_text("just_a_string")
    assert _load_catalog_from_file(str(f)) is None


# --- End-to-end: group claim -> auth.py -> route ---------------------------

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


def _mint(priv_pem: bytes, username: str = "carol") -> str:
    now = int(time.time())
    claims = {
        "sub": f"{username}-keycloak-uuid",
        "preferred_username": username,
        "iss": _ISSUER,
        "aud": settings.oidc_audience,
        "iat": now,
        "exp": now + 300,
    }
    return jwt.encode(claims, priv_pem, algorithm="RS256", headers={"kid": _KID})


def _gateway_headers(
    username: str = "carol", groups: list[str] | None = None
) -> dict[str, str]:
    headers = {
        "X-Auth-Request-Preferred-Username": username,
        "X-Report-Viewer-Gateway": os.environ["REPORT_VIEWER_GATEWAY_SECRET"],
    }
    if groups is not None:
        headers["X-Auth-Request-Groups"] = ",".join(groups)
    return headers


def _create_search(client, headers: dict[str, str], fake_trino) -> str:
    fake_trino(
        ["primary_report_identifier", "accession_number"],
        [{"primary_report_identifier": "s3://x/1", "accession_number": "ACC1"}],
    )
    fake_trino(["n"], [{"n": 1}])
    r = client.post(
        "/api/searches",
        json={
            "sql": "SELECT primary_report_identifier, accession_number FROM reports_latest"
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_actions_endpoint_hides_admin_action_without_group(
    client, fake_trino, catalog_with_admin_action
):
    headers = _gateway_headers(groups=[])
    search_id = _create_search(client, headers, fake_trino)

    r = client.get(f"/api/searches/{search_id}/actions", headers=headers)
    assert r.status_code == 200, r.text
    ids = {a["id"] for a in r.json()}
    assert "admin-only-demo" not in ids


def test_actions_endpoint_shows_admin_action_with_group(
    client, fake_trino, catalog_with_admin_action
):
    headers = _gateway_headers(groups=[_ADMIN_GROUP])
    search_id = _create_search(client, headers, fake_trino)

    r = client.get(f"/api/searches/{search_id}/actions", headers=headers)
    assert r.status_code == 200, r.text
    ids = {a["id"] for a in r.json()}
    assert "admin-only-demo" in ids


def test_actions_endpoint_404s_for_someone_elses_search(client, fake_trino):
    owner_headers = _gateway_headers(username="carol")
    search_id = _create_search(client, owner_headers, fake_trino)

    other_headers = _gateway_headers(username="dave", groups=[_ADMIN_GROUP])
    r = client.get(f"/api/searches/{search_id}/actions", headers=other_headers)
    assert r.status_code == 404


def test_bearer_jwt_path_never_grants_groups(
    client, keypair, fake_trino, catalog_with_admin_action
):
    """The Bearer JWT path (Path 1, used by OWUI's server-side tool calls)
    carries no group claim at all - a group-gated action must never be
    visible through it, no matter what the token otherwise contains.
    Locks in the architecture fix: group gating only works for the SPA's
    own oauth2-proxy-header requests (Path 2)."""
    priv, _ = keypair
    # Same username on both paths (both default to "carol") so the search
    # is found - the only thing under test is whether the admin action
    # leaks through, not ownership.
    token = _mint(priv)
    search_id = _create_search(client, _gateway_headers(), fake_trino)

    r = client.get(
        f"/api/searches/{search_id}/actions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    ids = {a["id"] for a in r.json()}
    assert "admin-only-demo" not in ids


# --- invoke: the generic backend-call proxy --------------------------------


class _FakeInvokeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient so no real network call happens -
    proves report-viewer's proxy logic without needing xnat-explore-poc
    (or any real service) actually running."""

    last_call: dict | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeAsyncClient.last_call = {"url": url, "json": json, "headers": headers}
        return _FakeInvokeResponse({"url": "https://xnat.example.org?t=123"})


@pytest.fixture
def catalog_with_backend_call_action(monkeypatch, tmp_path):
    demo_catalog = [
        *actions._DEFAULT_CATALOG,
        ActionDescriptor(
            id="explore-xnat-demo",
            title="Explore in XNAT (test)",
            action_type="backend-call",
            endpoint_url="http://xnat-explore-poc.test.svc.cluster.local:8000/invoke",
        ),
    ]
    monkeypatch.setattr(actions, "_CATALOG", demo_catalog)
    # invoke tokens and assertion-signing keys live in a Secret-backed
    # volume, keyed by action id - see actions.load_invoke_token()/
    # mint_user_assertion().
    monkeypatch.setattr(settings, "action_tokens_path", str(tmp_path))
    (tmp_path / "explore-xnat-demo").write_text("test-invoke-token")
    (tmp_path / "explore-xnat-demo.assertion-key").write_text("test-assertion-key")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.last_call = None
    return demo_catalog


def test_invoke_backend_call_action_returns_url(
    client, keypair, fake_trino, catalog_with_backend_call_action
):
    priv, _ = keypair
    token = _mint(priv)
    bearer_headers = {"Authorization": f"Bearer {token}"}
    search_id = _create_search(client, bearer_headers, fake_trino)

    fake_trino(
        ["primary_report_identifier", "accession_number"],
        [
            {"primary_report_identifier": "s3://x/1", "accession_number": "ACC1"},
            {"primary_report_identifier": "s3://x/2", "accession_number": None},
        ],
    )
    r = client.post(
        f"/api/searches/{search_id}/actions/explore-xnat-demo/invoke",
        headers=bearer_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"url": "https://xnat.example.org?t=123"}

    # Forwarded the shared secret and the resolved cohort - concrete
    # per-report ids, not the raw sql alone - never the token itself back
    # to the caller.
    call = _FakeAsyncClient.last_call
    assert call["headers"]["X-Report-Viewer-Action-Token"] == "test-invoke-token"
    assert call["json"]["search_id"] == search_id

    # Bearer-path caller ("carol", via _mint) carries no group claim, but
    # still gets a verifiable assertion - signed with a DIFFERENT key from
    # the invoke token above.
    assertion = call["headers"]["X-Report-Viewer-User-Assertion"]
    claims = jwt.decode(assertion, "test-assertion-key", algorithms=["HS256"])
    assert claims["sub"] == "carol"
    assert claims["groups"] == []
    assert claims["search_id"] == search_id
    assert claims["action_id"] == "explore-xnat-demo"
    assert call["json"]["reports"] == [
        {"primary_report_identifier": "s3://x/1", "accession_number": "ACC1"},
        {"primary_report_identifier": "s3://x/2", "accession_number": None},
    ]
    assert call["json"]["cohort_truncated"] is False


def test_invoke_unknown_action_404s(
    client, keypair, fake_trino, catalog_with_backend_call_action
):
    priv, _ = keypair
    token = _mint(priv)
    bearer_headers = {"Authorization": f"Bearer {token}"}
    search_id = _create_search(client, bearer_headers, fake_trino)

    r = client.post(
        f"/api/searches/{search_id}/actions/does-not-exist/invoke",
        headers=bearer_headers,
    )
    assert r.status_code == 404


def test_invoke_non_backend_call_action_404s(
    client, fake_trino, catalog_with_admin_action
):
    """open-url (and client) actions aren't invokable - they're static or
    page-local, there's nothing for the proxy to call. Uses the
    group-header path (not Bearer) since the action under test is
    group-gated and only that path can make it visible in the first
    place."""
    headers = _gateway_headers(groups=[_ADMIN_GROUP])
    search_id = _create_search(client, headers, fake_trino)

    r = client.post(
        f"/api/searches/{search_id}/actions/admin-only-demo/invoke",
        headers=headers,
    )
    assert r.status_code == 404


def test_invoke_backend_call_target_error_returns_502(
    client, keypair, fake_trino, catalog_with_backend_call_action, monkeypatch
):
    class _FailingAsyncClient(_FakeAsyncClient):
        async def post(self, url, json=None, headers=None):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", _FailingAsyncClient)

    priv, _ = keypair
    token = _mint(priv)
    bearer_headers = {"Authorization": f"Bearer {token}"}
    search_id = _create_search(client, bearer_headers, fake_trino)

    fake_trino(
        ["primary_report_identifier", "accession_number"],
        [{"primary_report_identifier": "s3://x/1", "accession_number": "ACC1"}],
    )
    r = client.post(
        f"/api/searches/{search_id}/actions/explore-xnat-demo/invoke",
        headers=bearer_headers,
    )
    assert r.status_code == 502


def test_invoke_cohort_id_query_failure_returns_502(
    client, keypair, fake_trino, catalog_with_backend_call_action
):
    """The cohort-id lookup failing is fatal, same as GET /rows - a
    backend-call action that can't resolve its own cohort shouldn't
    silently invoke with an empty one."""
    priv, _ = keypair
    token = _mint(priv)
    bearer_headers = {"Authorization": f"Bearer {token}"}
    search_id = _create_search(client, bearer_headers, fake_trino)

    fake_trino.error("some trino error")
    r = client.post(
        f"/api/searches/{search_id}/actions/explore-xnat-demo/invoke",
        headers=bearer_headers,
    )
    assert r.status_code == 502
