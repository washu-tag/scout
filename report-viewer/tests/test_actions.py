"""Tests for issue #739's action-descriptor contract and role filtering.

Most of this is pure-function coverage over `actions.py` (no Postgres/JWKS
needed). The end-to-end tests drive the real `/api/searches/{id}/actions`
endpoint through the single Bearer JWT auth path (Path 2 - oauth2-proxy
headers + gateway secret - was retired once Path 1 started reaching 100% of
inbound traffic, including the SPA's own requests). See conftest.py for the
shared JWKS-mocking/`mint_token` fixtures these tests use.
"""

from __future__ import annotations

import base64

import httpx
import pytest
import yaml
from pydantic import ValidationError

from scout_report_viewer import actions, kube
from scout_report_viewer.actions import (
    ActionDescriptor,
    is_safe_action_url,
    _load_catalog_from_file,
    list_actions,
    load_invoke_token,
)
from scout_report_viewer.config import settings

from .conftest import mint_token

# --- Pure-function tests: no fixtures required ---------------------------


async def test_default_catalog_matches_shipped_toolbar():
    """The no-ConfigMap-mounted floor must be exactly today's real
    toolbar (explain-search, download-csv) - see actions.py's
    _DEFAULT_CATALOG docstring. Demo/example entries belong in a test's
    own monkeypatched catalog, not this fallback."""
    ids = {a.id for a in await list_actions(frozenset())}
    assert ids == {"explain-search", "download-csv"}


_ADMIN_ROLE = "report-viewer-admin"


@pytest.fixture
def catalog_with_admin_action(monkeypatch):
    """A temporary catalog carrying a role-gated entry, for tests that
    need to exercise gating without it living in the shipped default."""
    demo_catalog = [
        *actions._DEFAULT_CATALOG,
        ActionDescriptor(
            id="admin-only-demo",
            title="Admin Only (test)",
            action_type="open-url",
            url="https://example.org/admin",
            required_role=_ADMIN_ROLE,
        ),
    ]
    monkeypatch.setattr(actions, "_CATALOG", demo_catalog)
    return demo_catalog


async def test_list_actions_excludes_role_gated_action_without_role(
    catalog_with_admin_action,
):
    ids = {a.id for a in await list_actions(frozenset())}
    assert "admin-only-demo" not in ids
    assert "download-csv" in ids


async def test_list_actions_includes_role_gated_action_with_role(
    catalog_with_admin_action,
):
    ids = {a.id for a in await list_actions(frozenset({_ADMIN_ROLE}))}
    assert "admin-only-demo" in ids


async def test_list_actions_sorted_by_weight(catalog_with_admin_action):
    result = await list_actions(frozenset({_ADMIN_ROLE}))
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
    assert is_safe_action_url(url) is expected


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


# --- End-to-end: resource_access roles claim -> auth.py -> route -----------
#
# keypair/JWKS mocking come from conftest.py (shared with test_jwt_auth.py).


def _bearer_headers(
    priv_pem: bytes, username: str = "carol", roles: list[str] | None = None
) -> dict[str, str]:
    overrides = {"sub": f"{username}-keycloak-uuid", "preferred_username": username}
    if roles is not None:
        overrides["resource_access"] = {"report-viewer": {"roles": roles}}
    token = mint_token(priv_pem, **overrides)
    return {"Authorization": f"Bearer {token}"}


def _create_search(client, headers: dict[str, str], fake_trino) -> str:
    fake_trino(
        ["primary_report_identifier", "accession_number"],
        [{"primary_report_identifier": "s3://x/1", "accession_number": "ACC1"}],
    )
    r = client.post(
        "/api/searches",
        json={
            "sql": "SELECT primary_report_identifier, accession_number FROM reports_latest"
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_actions_endpoint_hides_admin_action_without_role(
    client, keypair, fake_trino, catalog_with_admin_action
):
    priv, _ = keypair
    headers = _bearer_headers(priv, roles=[])
    search_id = _create_search(client, headers, fake_trino)

    r = client.get(f"/api/searches/{search_id}/actions", headers=headers)
    assert r.status_code == 200, r.text
    ids = {a["id"] for a in r.json()}
    assert "admin-only-demo" not in ids


def test_actions_endpoint_shows_admin_action_with_role(
    client, keypair, fake_trino, catalog_with_admin_action
):
    priv, _ = keypair
    headers = _bearer_headers(priv, roles=[_ADMIN_ROLE])
    search_id = _create_search(client, headers, fake_trino)

    r = client.get(f"/api/searches/{search_id}/actions", headers=headers)
    assert r.status_code == 200, r.text
    ids = {a["id"] for a in r.json()}
    assert "admin-only-demo" in ids


def test_actions_endpoint_404s_for_someone_elses_search(client, keypair, fake_trino):
    priv, _ = keypair
    owner_headers = _bearer_headers(priv, username="carol")
    search_id = _create_search(client, owner_headers, fake_trino)

    other_headers = _bearer_headers(priv, username="dave", roles=[_ADMIN_ROLE])
    r = client.get(f"/api/searches/{search_id}/actions", headers=other_headers)
    assert r.status_code == 404


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
    # Invoke tokens live in a Secret-backed volume, keyed by action id -
    # see actions.load_invoke_token().
    monkeypatch.setattr(settings, "action_tokens_path", str(tmp_path))
    (tmp_path / "explore-xnat-demo").write_text("test-invoke-token")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.last_call = None
    return demo_catalog


def test_invoke_backend_call_action_returns_url(
    client, keypair, fake_trino, catalog_with_backend_call_action
):
    priv, _ = keypair
    bearer_headers = _bearer_headers(priv)
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
    assert call["json"]["username"] == "carol"
    assert call["json"]["reports"] == [
        {"primary_report_identifier": "s3://x/1", "accession_number": "ACC1"},
        {"primary_report_identifier": "s3://x/2", "accession_number": None},
    ]
    assert call["json"]["cohort_truncated"] is False


def test_invoke_backend_call_action_filters_to_visible_report_ids(
    client, keypair, fake_trino, catalog_with_backend_call_action
):
    """The SPA's currently client-side-filtered rows narrow what gets
    forwarded, so a filtered view and the action agree on scope instead
    of the action silently reaching past an active filter."""
    priv, _ = keypair
    bearer_headers = _bearer_headers(priv)
    search_id = _create_search(client, bearer_headers, fake_trino)

    fake_trino(
        ["primary_report_identifier", "accession_number"],
        [
            {"primary_report_identifier": "s3://x/1", "accession_number": "ACC1"},
            {"primary_report_identifier": "s3://x/2", "accession_number": "ACC2"},
        ],
    )
    r = client.post(
        f"/api/searches/{search_id}/actions/explore-xnat-demo/invoke",
        headers=bearer_headers,
        json={"visible_report_ids": ["s3://x/1"]},
    )
    assert r.status_code == 200, r.text
    call = _FakeAsyncClient.last_call
    assert call["json"]["reports"] == [
        {"primary_report_identifier": "s3://x/1", "accession_number": "ACC1"},
    ]


def test_invoke_backend_call_action_ignores_unauthorized_visible_ids(
    client, keypair, fake_trino, catalog_with_backend_call_action
):
    """A submitted id that isn't in the Trino-resolved cohort (tampered
    request, stale client state) is silently dropped, never forwarded -
    the submitted ids are never trusted on their own."""
    priv, _ = keypair
    bearer_headers = _bearer_headers(priv)
    search_id = _create_search(client, bearer_headers, fake_trino)

    fake_trino(
        ["primary_report_identifier", "accession_number"],
        [{"primary_report_identifier": "s3://x/1", "accession_number": "ACC1"}],
    )
    r = client.post(
        f"/api/searches/{search_id}/actions/explore-xnat-demo/invoke",
        headers=bearer_headers,
        json={"visible_report_ids": ["s3://not-in-cohort"]},
    )
    assert r.status_code == 200, r.text
    call = _FakeAsyncClient.last_call
    assert call["json"]["reports"] == []


def test_invoke_unknown_action_404s(
    client, keypair, fake_trino, catalog_with_backend_call_action
):
    priv, _ = keypair
    bearer_headers = _bearer_headers(priv)
    search_id = _create_search(client, bearer_headers, fake_trino)

    r = client.post(
        f"/api/searches/{search_id}/actions/does-not-exist/invoke",
        headers=bearer_headers,
    )
    assert r.status_code == 404


def test_invoke_non_backend_call_action_404s(
    client, keypair, fake_trino, catalog_with_admin_action
):
    """open-url (and client) actions aren't invokable - they're static or
    page-local, there's nothing for the proxy to call. The action under
    test is role-gated, so the caller needs that role to see it in the
    first place."""
    priv, _ = keypair
    headers = _bearer_headers(priv, roles=[_ADMIN_ROLE])
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
    bearer_headers = _bearer_headers(priv)
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


def test_invoke_backend_call_rejects_unsafe_response_url(
    client, keypair, fake_trino, catalog_with_backend_call_action, monkeypatch
):
    """A compromised or buggy target returning javascript:... must not
    reach the frontend, which opens whatever url comes back as an anchor
    href (openResult.ts)."""

    class _UnsafeUrlAsyncClient(_FakeAsyncClient):
        async def post(self, url, json=None, headers=None):
            return _FakeInvokeResponse({"url": "javascript:alert(1)"})

    monkeypatch.setattr(httpx, "AsyncClient", _UnsafeUrlAsyncClient)

    priv, _ = keypair
    bearer_headers = _bearer_headers(priv)
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
    bearer_headers = _bearer_headers(priv)
    search_id = _create_search(client, bearer_headers, fake_trino)

    fake_trino.error("some trino error")
    r = client.post(
        f"/api/searches/{search_id}/actions/explore-xnat-demo/invoke",
        headers=bearer_headers,
    )
    assert r.status_code == 502


# --- Discovery: actions other charts publish as labeled ConfigMaps --------


def _configmap(namespace: str, name: str, **docs: str) -> dict:
    return {
        "metadata": {
            "namespace": namespace,
            "name": name,
            "uid": f"{namespace}-{name}",
            "resourceVersion": "1",
        },
        "data": docs,
    }


_SEND_TO_XNAT = """
apiVersion: report-viewer.scout.xnat.org/v1alpha1
kind: ActionCatalog
actions:
  - id: send-to-xnat
    title: Send to XNAT
    weight: 50
    actionType: backend-call
    endpointUrl: http://datasets-invoke.datasets.svc.cluster.local:8002/invoke
    requiredRole: datasets-user
    invokeTokenSecret: {name: datasets-invoke-token}
"""


def test_parse_discovered_reads_actions_and_token_refs():
    found = actions.parse_discovered(
        [_configmap("datasets", "datasets-actions", **{"actions.yaml": _SEND_TO_XNAT})],
        reserved_ids=set(),
    )
    (action,) = found.actions
    assert action.id == "send-to-xnat"
    assert action.action_type == "backend-call"
    assert action.required_role == "datasets-user"
    assert found.tokens == {
        "send-to-xnat": actions.TokenRef("datasets", "datasets-invoke-token", "token")
    }


@pytest.mark.parametrize(
    "doc",
    [
        "not: [valid",
        "apiVersion: report-viewer.scout.xnat.org/v1beta9\nkind: ActionCatalog\nactions: []",
        "apiVersion: report-viewer.scout.xnat.org/v1alpha1\nkind: Catalog\nactions: []",
        "apiVersion: report-viewer.scout.xnat.org/v1alpha1\nkind: ActionCatalog\nactions: {}",
        "- id: a-list-not-a-document",
        "[" * 5000,
    ],
)
def test_parse_discovered_skips_a_bad_document_but_keeps_its_siblings(doc):
    found = actions.parse_discovered(
        [
            _configmap(
                "datasets", "x", **{"a-bad.yaml": doc, "b-good.yaml": _SEND_TO_XNAT}
            )
        ],
        reserved_ids=set(),
    )
    assert [a.id for a in found.actions] == ["send-to-xnat"]


@pytest.mark.parametrize(
    "entry",
    [
        # A client action needs a frontend handler, so it can't be published.
        {"id": "x", "title": "X", "actionType": "client", "clientHandler": "x"},
        {"id": "x", "title": "X", "url": "javascript:alert(1)"},
        {"id": "Bad_ID", "title": "X", "url": "https://example.org"},
        {"id": "x", "title": "", "url": "https://example.org"},
        # An unknown field, here the snake_case spelling, rejects the entry
        # rather than publishing it without its role gate.
        {
            "id": "x",
            "title": "X",
            "url": "https://example.org",
            "required_role": "report-viewer-admin",
        },
        {
            "id": "x",
            "title": "X",
            "url": "https://example.org",
            "invokeTokenSecret": {"name": "s"},
        },
        # The Secret's namespace is never taken from the document.
        {
            "id": "x",
            "title": "X",
            "actionType": "backend-call",
            "endpointUrl": "http://t.ns.svc:8000/invoke",
            "invokeTokenSecret": {"name": "s", "namespace": "other"},
        },
        # A backend-call needs its token Secret, and a Service in its own
        # namespace: not another namespace's, not outside the cluster.
        {
            "id": "x",
            "title": "X",
            "actionType": "backend-call",
            "endpointUrl": "http://t.ns.svc:8000/invoke",
        },
        *(
            {
                "id": "x",
                "title": "X",
                "actionType": "backend-call",
                "endpointUrl": url,
                "invokeTokenSecret": {"name": "s"},
            }
            for url in (
                "http://t.other.svc.cluster.local:8000/invoke",
                "https://example.org/invoke",
                "http://t.ns:8000/invoke",
                "http://t.ns.svc.example.org/invoke",
                "http://10.0.0.1:8000/invoke",
            )
        ),
    ],
)
def test_parse_discovered_skips_an_invalid_entry_but_keeps_the_rest(entry):
    doc = yaml.safe_dump(
        {
            "apiVersion": actions.CATALOG_API_VERSION,
            "kind": actions.CATALOG_KIND,
            "actions": [entry, {"id": "ok", "title": "OK", "url": "https://ok.org"}],
        }
    )
    found = actions.parse_discovered(
        [_configmap("ns", "cm", **{"a.yaml": doc})], reserved_ids=set()
    )
    assert [a.id for a in found.actions] == ["ok"]


@pytest.mark.parametrize(
    "url", ["http://t.ns.svc:8000/invoke", "https://t.ns.svc.cluster.local/invoke"]
)
def test_parse_discovered_accepts_a_service_in_its_own_namespace(url):
    doc = yaml.safe_dump(
        {
            "apiVersion": actions.CATALOG_API_VERSION,
            "kind": actions.CATALOG_KIND,
            "actions": [
                {
                    "id": "x",
                    "title": "X",
                    "actionType": "backend-call",
                    "endpointUrl": url,
                    "invokeTokenSecret": {"name": "s"},
                }
            ],
        }
    )
    found = actions.parse_discovered(
        [_configmap("ns", "cm", **{"a.yaml": doc})], reserved_ids=set()
    )
    assert [a.endpoint_url for a in found.actions] == [url]


def test_parse_discovered_rejects_taken_ids_in_a_stable_order():
    doc = _SEND_TO_XNAT.replace("datasets-invoke-token", "{ns}-token").replace(
        "datasets-invoke.datasets.", "datasets-invoke.{ns}."
    )
    found = actions.parse_discovered(
        [
            _configmap("zeta", "cm", **{"a.yaml": doc.replace("{ns}", "zeta")}),
            _configmap("alpha", "cm", **{"a.yaml": doc.replace("{ns}", "alpha")}),
            _configmap(
                "alpha",
                "cm2",
                **{
                    "a.yaml": doc.replace("send-to-xnat", "download-csv").replace(
                        "{ns}", "alpha"
                    )
                },
            ),
        ],
        reserved_ids={"download-csv"},
    )
    assert [a.id for a in found.actions] == ["send-to-xnat"]
    assert found.tokens["send-to-xnat"].namespace == "alpha"


@pytest.fixture
def discovery(monkeypatch):
    """Discovery on, a fresh snapshot, and a fake Kubernetes API that serves
    `configmaps` (per namespace, None = all) and records each list."""
    monkeypatch.setattr(settings, "action_discovery_enabled", True)
    monkeypatch.setattr(settings, "action_discovery_namespace", "ALL")
    monkeypatch.setattr(actions, "_snapshot", actions._Snapshot())
    state = {
        "configmaps": {
            None: [_configmap("datasets", "cm", **{"a.yaml": _SEND_TO_XNAT})]
        },
        "lists": [],
        "fail": False,
        "secrets": {},
    }

    async def list_configmaps(label_selector, namespace):
        assert label_selector == f"{actions.DISCOVERY_LABEL}=true"
        state["lists"].append(namespace)
        if state["fail"]:
            raise kube.KubeError("API unreachable")
        return state["configmaps"].get(namespace, [])

    async def secret_value(namespace, name, key):
        try:
            return state["secrets"][(namespace, name, key)]
        except KeyError:
            raise kube.KubeError("forbidden") from None

    monkeypatch.setattr(kube, "list_configmaps", list_configmaps)
    monkeypatch.setattr(kube, "secret_value", secret_value)
    return state


async def test_discovery_off_lists_nothing(discovery, monkeypatch):
    monkeypatch.setattr(settings, "action_discovery_enabled", False)
    ids = {a.id for a in await list_actions(frozenset({"datasets-user"}))}
    assert ids == {"explain-search", "download-csv"}
    assert discovery["lists"] == []


async def test_discovered_action_is_role_gated_and_merged(discovery):
    assert "send-to-xnat" not in {a.id for a in await list_actions(frozenset())}
    visible = await list_actions(frozenset({"datasets-user"}))
    assert [a.id for a in visible] == ["explain-search", "download-csv", "send-to-xnat"]


async def test_discovery_relists_only_after_the_ttl(discovery, monkeypatch):
    await list_actions(frozenset())
    await list_actions(frozenset())
    assert discovery["lists"] == [None]
    monkeypatch.setattr(actions._snapshot, "checked_at", float("-inf"))
    discovery["configmaps"] = {None: []}
    assert {a.id for a in await list_actions(frozenset({"datasets-user"}))} == {
        "explain-search",
        "download-csv",
    }


async def test_discovery_failure_keeps_the_last_catalog(discovery, monkeypatch):
    await list_actions(frozenset())
    monkeypatch.setattr(actions._snapshot, "checked_at", float("-inf"))
    discovery["fail"] = True
    ids = {a.id for a in await list_actions(frozenset({"datasets-user"}))}
    assert "send-to-xnat" in ids


async def test_discovery_error_of_any_kind_keeps_the_chart_catalog(
    discovery, monkeypatch
):
    await list_actions(frozenset())
    monkeypatch.setattr(actions._snapshot, "checked_at", float("-inf"))

    def broken(*args):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(actions, "parse_discovered", broken)
    discovery["configmaps"][None][0]["metadata"]["resourceVersion"] = "2"
    ids = [a.id for a in await list_actions(frozenset({"datasets-user"}))]
    assert ids == ["explain-search", "download-csv", "send-to-xnat"]
    assert discovery["lists"] == [None, None]
    await list_actions(frozenset())
    assert discovery["lists"] == [None, None]


async def test_discovery_lists_each_configured_namespace(discovery, monkeypatch):
    monkeypatch.setattr(settings, "action_discovery_namespace", "datasets, other")
    await list_actions(frozenset())
    assert sorted(discovery["lists"]) == ["datasets", "other"]


async def test_discovered_token_is_read_from_its_own_namespace(discovery):
    discovery["secrets"][("datasets", "datasets-invoke-token", "token")] = "s3cret"
    catalog = await actions.current_catalog()
    assert await catalog.invoke_token("send-to-xnat") == "s3cret"


async def test_discovered_action_never_uses_a_chart_token_file(
    discovery, tmp_path, monkeypatch
):
    discovery["secrets"][("datasets", "datasets-invoke-token", "token")] = "s3cret"
    monkeypatch.setattr(settings, "action_tokens_path", str(tmp_path))
    (tmp_path / "send-to-xnat").write_text("chart-token")
    catalog = await actions.current_catalog()
    assert await catalog.invoke_token("send-to-xnat") == "s3cret"


async def test_unreadable_discovered_token_raises(discovery):
    catalog = await actions.current_catalog()
    with pytest.raises(kube.KubeError):
        await catalog.invoke_token("send-to-xnat")


def test_invoke_discovered_action_sends_its_secret_token(
    client, keypair, fake_trino, discovery, monkeypatch
):
    discovery["secrets"][("datasets", "datasets-invoke-token", "token")] = "s3cret"
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.last_call = None
    priv, _ = keypair
    headers = _bearer_headers(priv, roles=["datasets-user"])
    search_id = _create_search(client, headers, fake_trino)
    fake_trino(
        ["primary_report_identifier", "accession_number"],
        [{"primary_report_identifier": "s3://x/1", "accession_number": "ACC1"}],
    )
    r = client.post(
        f"/api/searches/{search_id}/actions/send-to-xnat/invoke", headers=headers
    )
    assert r.status_code == 200, r.text
    call = _FakeAsyncClient.last_call
    assert (
        call["url"] == "http://datasets-invoke.datasets.svc.cluster.local:8002/invoke"
    )
    assert call["headers"]["X-Report-Viewer-Action-Token"] == "s3cret"


def test_invoke_discovered_action_without_its_token_is_502(
    client, keypair, fake_trino, discovery, monkeypatch
):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.last_call = None
    priv, _ = keypair
    headers = _bearer_headers(priv, roles=["datasets-user"])
    search_id = _create_search(client, headers, fake_trino)
    fake_trino(
        ["primary_report_identifier", "accession_number"],
        [{"primary_report_identifier": "s3://x/1", "accession_number": "ACC1"}],
    )
    r = client.post(
        f"/api/searches/{search_id}/actions/send-to-xnat/invoke", headers=headers
    )
    assert r.status_code == 502
    assert _FakeAsyncClient.last_call is None


# --- kube -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("10.43.0.1", "https://10.43.0.1:443"),
        ("fd00:10:96::1", "https://[fd00:10:96::1]:443"),
    ],
)
def test_base_url_brackets_an_ipv6_host(monkeypatch, host, expected):
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", host)
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    assert kube._base_url() == expected
    httpx.URL(kube._base_url())


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"token": base64.b64encode(b" s3cret\n").decode()}, "s3cret"),
        ({}, None),
        ({"token": ""}, None),
        ({"token": "%%%"}, None),
    ],
)
async def test_secret_value(monkeypatch, data, expected):
    async def get(path):
        assert path == "/api/v1/namespaces/datasets/secrets/datasets-invoke-token"
        return {"data": data}

    monkeypatch.setattr(kube, "_get", get)
    if expected is None:
        with pytest.raises(kube.KubeError):
            await kube.secret_value("datasets", "datasets-invoke-token", "token")
    else:
        assert (
            await kube.secret_value("datasets", "datasets-invoke-token", "token")
            == expected
        )
