import base64
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from scout_app_manager.compose import Site
from scout_app_manager.k8s import ApiError
from scout_app_manager.schema import (
    API_VERSION,
    FRAGMENT_LABEL,
    FRAGMENT_LABEL_VALUE,
    KIND,
)
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


def configmap_yaml(
    name: str = "my-service-keycloak",
    namespace: str | None = "my-service",
    *,
    labelled: bool = True,
    data: dict[str, str] | None = None,
) -> str:
    """The shape an author actually has in their chart: a wrapping ConfigMap."""
    metadata: dict = {"name": name}
    if namespace is not None:
        metadata["namespace"] = namespace
    if labelled:
        metadata["labels"] = {FRAGMENT_LABEL: FRAGMENT_LABEL_VALUE}
    return yaml.safe_dump(
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": metadata,
            "data": data if data is not None else {"fragment.yaml": fragment_yaml()},
        },
        sort_keys=False,
    )


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
        # Stands in for the realm attribute config-cli writes after an import.
        self.realm_checksum: str | None = None

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

    def set_secret(self, namespace, name, values: dict[str, str]):
        """Write a Secret the way the API stores one, bumping resourceVersion.

        The shape matters: the reconciler reads `data` and
        `metadata.resourceVersion` off a single GET, and the second is how it
        notices a rotation.
        """
        key = (namespace, name)
        version = int((self.secrets.get(key) or {}).get("_version", 0)) + 1
        self.secrets[key] = {
            "metadata": {"name": name, "resourceVersion": str(version)},
            "data": {
                k: base64.b64encode(v.encode()).decode() for k, v in values.items()
            },
            "_version": version,
        }

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
        if self.job_succeeds:
            # What config-cli would leave on the realm: a checksum over the
            # document it just imported. A test simulates another writer by
            # assigning realm_checksum something else.
            document = (
                (self.configmaps.get((namespace, "keycloak-config-composed")) or {})
                .get("data", {})
                .get("scout-realm.json", "")
            )
            self.realm_checksum = hashlib.sha256(document.encode()).hexdigest()
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


class FakeKeycloak:
    """The realm's own record of what was last imported into it.

    Backed by the FakeClient so an apply moves it the way a real one does;
    `readable = False` is Keycloak being unreachable, which must read as "we do
    not know" rather than as drift.
    """

    def __init__(self, client: FakeClient):
        self.client = client
        self.readable = True
        self.reads = 0

    def import_checksum(self):
        self.reads += 1
        return self.client.realm_checksum if self.readable else None


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
    client.set_secret(
        "scout-core", "hello-keycloak-client", {"client-secret": "s3cret"}
    )
    # The base realm's own credentials. The miniature realm in `base_realm`
    # carries no `$(env:...)` tokens, so these are only here to be a realistic
    # reserved-name set and something for a rotation test to move.
    client.set_secret(
        "scout-core",
        "keycloak-client-secrets",
        {"oauth2_proxy": "op", "launchpad_client": "lp"},
    )
    service = AppManagerService(settings, client, keycloak=FakeKeycloak(client))
    # The normal running state; a test wanting the refused path clears it.
    service.state.discovery_synced = True
    return service, fragments, client


def status_of(state, ref):
    return next(f for f in state.fragments if f.ref == ref)
