import json
from pathlib import Path

import pytest
import yaml

from scout_app_manager.compose import Site
from scout_app_manager.k8s import ApiError
from scout_app_manager.schema import API_VERSION, KIND
from scout_app_manager.service import AppManagerService
from scout_app_manager.settings import Settings

DOMAIN = "scout.example.edu"


def fragment_yaml(
    name: str = "hello",
    client: str | None = None,
    *,
    top_level: dict | None = None,
    **client_overrides,
) -> str:
    """Build a fragment document from parts.

    Emits real YAML rather than string-building, so a test that injects an odd
    field cannot silently no-op when indentation drifts.
    """
    client = client or name
    spec: dict = {
        "clientId": client,
        "loginFlows": ["STANDARD"],
        "redirectUris": [f"https://{client}.${{domain}}/auth/callback"],
        "roles": [f"{client}-user", f"{client}-admin"],
        "grants": {
            "scout-user": [f"{client}-user"],
            "scout-admin": [f"{client}-admin"],
        },
        # Secret names are DNS labels; a clientId need not be.
        "secretRef": {"name": f"{client.replace('_', '-')}-keycloak-client"},
    }
    spec.update(client_overrides)
    for key in [k for k, v in spec.items() if v is None]:
        del spec[key]
    doc: dict = {"apiVersion": API_VERSION, "kind": KIND, "clients": [spec]}
    doc.update(top_level or {})
    return yaml.safe_dump(doc, sort_keys=False)


@pytest.fixture
def hello_yaml() -> str:
    return fragment_yaml("hello")


@pytest.fixture
def site() -> Site:
    return Site(domain=DOMAIN)


@pytest.fixture
def base_realm() -> dict:
    """A miniature of the real realm: the parts a fragment touches."""
    return {
        "realm": "scout",
        "clients": [
            {"clientId": "launchpad", "fullScopeAllowed": False},
            {"clientId": "oauth2-proxy", "fullScopeAllowed": False},
        ],
        "roles": {"client": {"launchpad": [{"name": "launchpad-user"}]}},
        "groups": [
            {"name": "scout-admin", "clientRoles": {"launchpad": ["launchpad-admin"]}},
            {"name": "scout-user", "clientRoles": {"launchpad": ["launchpad-user"]}},
        ],
        "clientScopeMappings": {
            "launchpad": [{"client": "launchpad", "roles": ["launchpad-user"]}]
        },
    }


def write_fragment(directory: Path, namespace: str, name: str, body: str) -> Path:
    """Write a fragment the way the kiwigrid sidecar would."""
    path = directory / f"namespace_{namespace}.configmap_{name}.fragment.yaml"
    path.write_text(body, encoding="utf-8")
    return path


class FakeClient:
    """Stands in for the cluster. Records writes so tests can assert on them."""

    def __init__(self):
        self.configmaps: dict[tuple[str, str], dict] = {}
        self.secrets: dict[tuple[str, str], dict] = {}
        self.jobs: dict[str, dict] = {}
        self.job_status: dict[str, dict] = {}
        self.created_jobs: list[str] = []
        self.deleted_jobs: list[str] = []
        self.job_succeeds = True

    def namespace(self) -> str:
        return "scout-core"

    def get_configmap(self, namespace, name):
        return self.configmaps.get((namespace, name))

    def put_configmap_data(self, namespace, name, data, labels=None):
        existing = self.configmaps.setdefault((namespace, name), {"data": {}})
        existing["data"].update(data)
        if labels:
            existing["labels"] = labels

    def get_secret(self, namespace, name):
        return self.secrets.get((namespace, name))

    def get_secret_value(self, namespace, name, key):
        secret = self.secrets.get((namespace, name)) or {}
        return (secret.get("plain") or {}).get(key)

    def put_secret(self, namespace, name, values, labels=None):
        self.secrets[(namespace, name)] = {"plain": dict(values), "labels": labels}

    def create_job(self, namespace, body):
        """409s on a name that is taken, exactly as the API does."""
        name = body["metadata"]["name"]
        if name in self.jobs:
            raise ApiError(409, f'jobs.batch "{name}" already exists')
        self.jobs[name] = body
        # The outcome is fixed when the Job is created, so a stale Job keeps
        # reporting the verdict of the run that produced it.
        self.job_status[name] = {"succeeded": 1} if self.job_succeeds else {"failed": 1}
        self.created_jobs.append(name)
        return body

    def get_job(self, namespace, name):
        if name not in self.jobs:
            return None
        return {"metadata": {"name": name}, "status": self.job_status.get(name, {})}

    def list_jobs(self, namespace, selector):
        return [{"metadata": {"name": n}} for n in self.jobs]

    def delete_job(self, namespace, name):
        self.deleted_jobs.append(name)
        self.jobs.pop(name, None)
        self.job_status.pop(name, None)

    def job_logs(self, namespace, name, tail=40):
        return "config-cli said no"


@pytest.fixture
def setup(tmp_path, base_realm, monkeypatch):
    fragments = tmp_path / "fragments"
    fragments.mkdir()
    realm_path = tmp_path / "scout-realm.json"
    realm_path.write_text(json.dumps(base_realm), encoding="utf-8")

    settings = Settings(
        fragment_dir=str(fragments),
        base_realm_path=str(realm_path),
        domain="scout.example.edu",
        namespace="scout-core",
        apply_mode="diff",
        resync_seconds=1,
    )
    client = FakeClient()
    # Every fragment client references a Secret in the service's namespace;
    # the fixture provisions the one the default fragment names.
    client.secrets[("scout-core", "hello-keycloak-client")] = {
        "plain": {"client-secret": "s3cret"},
        "labels": None,
    }
    service = AppManagerService(settings, client)
    # The normal running state; a test wanting the refused path clears it.
    service.state.discovery_synced = True
    return service, fragments, client


def status_of(state, ref):
    return next(f for f in state.fragments if f.ref == ref)
