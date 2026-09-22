"""In-memory stand-ins for Keycloak and Kubernetes.

Both record every call, because much of the suite asserts about writes that did
*not* happen and a fake that only models state cannot answer that.
`FakeKeycloak.calls` counts each admin operation by name, which is how the
reads a steady-state pass is allowed to make are pinned as well.

`FakeK8s.list_error` makes a LIST raise rather than return `[]`, which is the
only way to test that an unreachable API is not read as "every fragment has been
deleted". `secret_error` and `emit_failures` do the same for the other two reads
whose failure the service is expected to survive.
"""

from __future__ import annotations

import base64
import itertools
from collections import Counter

import pytest

from scout_keycloak_fragment_reconciler import translate
from scout_keycloak_fragment_reconciler.core import Reconciler
from scout_keycloak_fragment_reconciler.k8s import ApiError, TransportError
from scout_keycloak_fragment_reconciler.keycloak import KeycloakError
from scout_keycloak_fragment_reconciler.settings import Settings

HOSTNAME = "scout.example.edu"
TIERS = ["scout-user", "scout-admin"]

FRAGMENT = """
apiVersion: keycloak.scout.xnat.org/v1alpha1
kind: KeycloakFragment
clients:
  - clientId: {client_id}
    displayName: Hello Scout
    description: the fragment demo app
    appUrl: https://{client_id}.scout.example.edu
    redirectUris:
      - https://{client_id}.scout.example.edu/auth/callback
    roles:
      - {client_id}-user
      - {client_id}-admin
    secretRef:
      name: {client_id}-keycloak-client
    grants:
      scout-user: [{client_id}-user]
      scout-admin: [{client_id}-admin]
"""


def fragment_text(client_id: str = "hello") -> str:
    return FRAGMENT.format(client_id=client_id)


class FakeKeycloak:
    """Enough of the admin API to reconcile against, and a write log.

    Note what it has no methods for: groups, users, realm settings.
    """

    def __init__(self, tier_roles: list[str] | None = None) -> None:
        self._ids = itertools.count(1)
        self.realm_roles: dict[str, dict] = {}
        for name in tier_roles if tier_roles is not None else TIERS:
            self.realm_roles[name] = {
                "id": f"realm-{name}",
                "name": name,
                "composite": False,
            }
        self.clients: dict[str, dict] = {}
        self.roles: dict[str, dict[str, dict]] = {}
        self.mappers: dict[str, list[dict]] = {}
        self.secrets: dict[str, str] = {}
        # tier name -> {role id: role name}
        self.composites: dict[str, dict[str, str]] = {n: {} for n in self.realm_roles}
        self.writes: list[str] = []
        self.calls: Counter[str] = Counter()
        self.fail_on: dict[str, KeycloakError] = {}

    # --- test helpers ---------------------------------------------------

    def _uid(self, prefix: str) -> str:
        return f"{prefix}-{next(self._ids)}"

    def _check(self, op: str) -> None:
        self.calls[op] += 1
        if op in self.fail_on:
            raise self.fail_on[op]

    def seed_client(self, representation: dict, *, secret: str = "seeded") -> str:
        """Put a client in the realm without going through a write."""
        uuid = self._uid("client")
        self.clients[uuid] = {**representation, "id": uuid}
        self.roles[uuid] = {}
        self.mappers[uuid] = []
        self.secrets[uuid] = secret
        return uuid

    def seed_role(self, uuid: str, name: str) -> str:
        role_id = self._uid("role")
        self.roles[uuid][name] = {"id": role_id, "name": name}
        return role_id

    def edges(self, tier: str) -> set[str]:
        return set(self.composites.get(tier, {}).values())

    # --- the Admin surface ----------------------------------------------

    def realm_role(self, name: str) -> dict | None:
        self._check("realm_role")
        return self.realm_roles.get(name)

    def find_client(self, client_id: str) -> dict | None:
        self._check("find_client")
        for client in self.clients.values():
            if client.get("clientId") == client_id:
                return dict(client)
        return None

    def list_clients(self) -> list[dict]:
        self._check("list_clients")
        return [dict(c) for c in self.clients.values()]

    def create_client(self, representation: dict) -> str:
        self._check("create_client")
        self.writes.append(f"create_client:{representation['clientId']}")
        uuid = self._uid("client")
        self.clients[uuid] = {**representation, "id": uuid}
        self.roles[uuid] = {}
        self.mappers[uuid] = []
        self.secrets[uuid] = representation.get("secret", "")
        return uuid

    def update_client(self, uuid: str, representation: dict) -> None:
        self._check("update_client")
        self.writes.append(f"update_client:{representation['clientId']}")
        self.clients[uuid] = {**representation, "id": uuid}
        if "secret" in representation:
            self.secrets[uuid] = representation["secret"]

    def delete_client(self, uuid: str) -> None:
        self._check("delete_client")
        self.writes.append(f"delete_client:{self.clients[uuid]['clientId']}")
        del self.clients[uuid]
        self.roles.pop(uuid, None)
        self.mappers.pop(uuid, None)
        self.secrets.pop(uuid, None)

    def client_secret(self, uuid: str) -> str:
        self._check("client_secret")
        return self.secrets.get(uuid, "")

    def client_roles(self, uuid: str) -> list[dict]:
        self._check("client_roles")
        return [dict(r) for r in self.roles.get(uuid, {}).values()]

    def create_client_role(self, uuid: str, representation: dict) -> None:
        self._check("create_client_role")
        self.writes.append(f"create_role:{representation['name']}")
        self.seed_role(uuid, representation["name"])

    def delete_client_role(self, uuid: str, name: str) -> None:
        self._check("delete_client_role")
        self.writes.append(f"delete_role:{name}")
        role = self.roles[uuid].pop(name, None)
        if role:
            for edges in self.composites.values():
                edges.pop(role["id"], None)

    def protocol_mappers(self, uuid: str) -> list[dict]:
        self._check("protocol_mappers")
        return [dict(m) for m in self.mappers.get(uuid, [])]

    def create_protocol_mapper(self, uuid: str, representation: dict) -> None:
        self._check("create_protocol_mapper")
        self.writes.append(f"create_mapper:{representation['name']}")
        self.mappers[uuid].append({**representation, "id": self._uid("mapper")})

    def update_protocol_mapper(self, uuid: str, mapper_id: str, rep: dict) -> None:
        self._check("update_protocol_mapper")
        self.writes.append(f"update_mapper:{rep['name']}")
        self.mappers[uuid] = [
            {**rep, "id": mapper_id} if m["id"] == mapper_id else m
            for m in self.mappers[uuid]
        ]

    def delete_protocol_mapper(self, uuid: str, mapper_id: str) -> None:
        self._check("delete_protocol_mapper")
        self.writes.append(f"delete_mapper:{mapper_id}")
        self.mappers[uuid] = [m for m in self.mappers[uuid] if m["id"] != mapper_id]

    def tier_edges_for_client(self, tier: str, client_uuid: str) -> list[dict]:
        self._check("tier_edges_for_client")
        owned = {r["id"] for r in self.roles.get(client_uuid, {}).values()}
        return [
            {"id": rid, "name": name}
            for rid, name in self.composites.get(tier, {}).items()
            if rid in owned
        ]

    def add_tier_edges(self, tier: str, roles: list[dict]) -> None:
        self._check("add_tier_edges")
        for role in roles:
            self.writes.append(f"add_edge:{tier}->{role['name']}")
            # Additive, as the real endpoint is -- never a replace.
            self.composites.setdefault(tier, {})[role["id"]] = role["name"]

    def remove_tier_edges(self, tier: str, roles: list[dict]) -> None:
        self._check("remove_tier_edges")
        for role in roles:
            self.writes.append(f"remove_edge:{tier}->{role['name']}")
            self.composites.get(tier, {}).pop(role["id"], None)


class FakeK8s:
    def __init__(self) -> None:
        self.configmaps: list[dict] = []
        self.secrets: dict[tuple[str, str], dict] = {}
        self.events: list[dict] = []
        self.list_error: ApiError | None = None
        self.secret_error: ApiError | None = None
        self.lists = 0
        self.emit_attempts = 0
        # How many of the next reports fail to land, as the real client
        # reports them: swallowed, and answered with False.
        self.emit_failures = 0

    # --- test helpers ---------------------------------------------------

    def add_fragment(
        self,
        text: str,
        *,
        namespace: str = "demo",
        name: str = "hello-keycloak",
        key: str = "fragment.yaml",
    ) -> dict:
        item = {
            "metadata": {
                "namespace": namespace,
                "name": name,
                "uid": f"uid-{namespace}-{name}",
                "resourceVersion": "1",
            },
            "data": {key: text},
        }
        self.configmaps = [
            c
            for c in self.configmaps
            if (c["metadata"]["namespace"], c["metadata"]["name"]) != (namespace, name)
        ]
        self.configmaps.append(item)
        return item

    def remove_fragment(self, namespace: str = "demo", name: str = "hello-keycloak"):
        self.configmaps = [
            c
            for c in self.configmaps
            if (c["metadata"]["namespace"], c["metadata"]["name"]) != (namespace, name)
        ]

    def add_secret(
        self,
        value: str | bytes,
        *,
        namespace: str = "demo",
        name: str = "hello-keycloak-client",
        key: str = "client-secret",
    ) -> None:
        """Takes bytes as well as text, so a value that is not UTF-8 -- one of
        the ways a secretRef is unreadable -- is expressible."""
        raw = value.encode() if isinstance(value, str) else value
        self.secrets[(namespace, name)] = {
            "metadata": {"namespace": namespace, "name": name},
            "data": {key: base64.b64encode(raw).decode()},
        }

    def reasons(self) -> list[str]:
        return [e["reason"] for e in self.events]

    # --- the Client surface ---------------------------------------------

    def namespace(self) -> str:
        return "scout-core"

    def list_configmaps(self, label_selector: str) -> list[dict]:
        self.lists += 1
        if self.list_error is not None:
            raise self.list_error
        return [dict(c) for c in self.configmaps]

    def get_configmap(self, namespace: str, name: str) -> dict | None:
        for item in self.configmaps:
            meta = item["metadata"]
            if (meta["namespace"], meta["name"]) == (namespace, name):
                return dict(item)
        return None

    def get_secret(self, namespace: str, name: str) -> dict | None:
        if self.secret_error is not None:
            raise self.secret_error
        return self.secrets.get((namespace, name))

    def emit_event(self, *, involved, reason, message, timestamp, **kwargs) -> bool:
        self.emit_attempts += 1
        if self.emit_failures:
            self.emit_failures -= 1
            return False
        self.events.append(
            {"reason": reason, "message": message, "type": kwargs.get("event_type")}
        )
        return True


@pytest.fixture
def settings() -> Settings:
    return Settings(
        keycloak_url="http://keycloak:8080",
        realm="scout",
        client_id="fragment_reconciler_svc",
        client_secret="test",
        server_hostname=HOSTNAME,
        tier_roles=TIERS,
        # What both deploy lanes set. The field's own default is empty, which
        # reconciles nothing, so every test would pass vacuously.
        watched_namespaces=["ALL"],
        orphan_grace_seconds=300,
        resync_seconds=300,
    )


@pytest.fixture
def kc() -> FakeKeycloak:
    return FakeKeycloak()


@pytest.fixture
def k8s() -> FakeK8s:
    fake = FakeK8s()
    fake.add_fragment(fragment_text())
    fake.add_secret("hello-secret")
    return fake


@pytest.fixture
def reconciler(settings, k8s, kc) -> Reconciler:
    return Reconciler(settings, k8s, kc)


@pytest.fixture
def unowned_client() -> dict:
    """A base-realm client: right clientId, no stamp. Invariant 2's subject."""
    return {
        "clientId": "hello",
        "name": "Somebody else's hello",
        "enabled": True,
        "fullScopeAllowed": True,
        "attributes": {"owner": "the base realm"},
    }


__all__ = [
    "ApiError",
    "FakeK8s",
    "FakeKeycloak",
    "HOSTNAME",
    "KeycloakError",
    "TIERS",
    "TransportError",
    "fragment_text",
    "translate",
]
