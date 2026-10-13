"""Offline validation of the CI site's real TLS material and rendered auth overrides."""

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

import prepare_auth_site as auth


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


@pytest.fixture(scope="module")
def tls(tmp_path_factory):
    directory = tmp_path_factory.mktemp("auth-tls")
    result = subprocess.run(
        ["bash", str(HERE / "generate_auth_tls.sh"), str(directory)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    yield directory
    (directory / "key.pem").unlink(missing_ok=True)


@pytest.fixture
def prepared(tmp_path, tls):
    site = tmp_path / "site"
    site.mkdir()
    (site / "kustomization.yaml").write_text("resources: [scout-config-source.yaml]\n")
    roots = tmp_path / "roots.yaml"
    auth.prepare(site, tls / "ca.crt", HERE / "roots-apps-auth.yaml", roots)
    return site, roots


def test_ca_has_required_usage_and_does_not_leave_signing_key(tls):
    assert not (tls / "ca.key").exists()
    for host in ("scout.test", "keycloak.scout.test", "auth.scout.test"):
        result = subprocess.run(
            [
                "openssl",
                "verify",
                "-CAfile",
                str(tls / "ca.crt"),
                "-purpose",
                "sslserver",
                "-verify_hostname",
                host,
                str(tls / "cert.pem"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    wrong_host = subprocess.run(
        [
            "openssl",
            "verify",
            "-CAfile",
            str(tls / "ca.crt"),
            "-verify_hostname",
            "untrusted.example",
            str(tls / "cert.pem"),
        ],
        capture_output=True,
        text=True,
    )
    assert wrong_host.returncode != 0
    untrusted = subprocess.run(
        [
            "openssl",
            "verify",
            str(tls / "cert.pem"),
        ],
        capture_output=True,
        text=True,
    )
    assert untrusted.returncode != 0


def test_site_contains_public_ca_only_in_bootstrapped_namespaces(prepared, tls):
    site, _ = prepared
    cert = (tls / "ca.crt").read_text()
    docs = list(yaml.safe_load_all((site / "ci-ingress-ca.yaml").read_text()))
    assert {(d["kind"], d["metadata"]["namespace"]) for d in docs} == {
        ("ConfigMap", "scout-core"),
        ("ConfigMap", "kube-system"),
        ("Secret", "kube-system"),
    }
    for doc in docs:
        value = doc["data"]["ca.crt"]
        assert (
            base64.b64decode(value).decode() if doc["kind"] == "Secret" else value
        ) == cert
    assert "PRIVATE KEY" not in (site / "ci-ingress-ca.yaml").read_text()
    assert yaml.safe_load((site / "kustomization.yaml").read_text())["resources"] == [
        "scout-config-source.yaml",
        "ci-ingress-ca.yaml",
    ]


def test_private_key_and_tracked_output_are_rejected(tmp_path, prepared, tls):
    site, _ = prepared
    private = tmp_path / "combined.pem"
    private.write_text((tls / "ca.crt").read_text() + (tls / "key.pem").read_text())
    roots = HERE / "roots-apps-auth.yaml"
    try:
        with pytest.raises(ValueError, match="no private key"):
            auth.prepare(site, private, roots, tmp_path / "new.yaml")
    finally:
        private.unlink()
    with pytest.raises(ValueError, match="scratch copy"):
        auth.prepare(site, tls / "ca.crt", roots, roots)


def test_fixture_filters_match_existing_ansible_ci_inventory():
    inventory = yaml.safe_load(
        (REPO / ".github/ci_resources/inventory.yaml").read_text()
    )
    values = inventory["k3s_cluster"]["vars"]
    assert auth.ATTRIBUTE_FILTERS == values["trino_attribute_filters"]
    opa = auth.auth_patches()["apps-scout"]["opa"][0]
    operations = json.loads(opa["patch"])
    document = json.loads(
        next(p["value"] for p in operations if p["path"] == "/spec/values/data/json")
    )
    assert document["filtered_tables"] == values["trino_filtered_tables"]
    assert {v["table"] for v in document["baseline_hidden_tables"]} == {
        "${report_delta_table_name}_report_patient_mapping",
        "${report_delta_table_name}_report_patient_mapping_history",
    }


def test_opa_patch_preserves_new_and_changed_deployment_policy_data(tmp_path):
    release = yaml.safe_load(auth.OPA_RESOURCES.read_text())
    original = json.loads(release["spec"]["values"]["data"]["json"])
    original["baseline_hidden_tables"].append(
        {"catalog": "delta", "schema": "private", "table": "future_sensitive_table"}
    )
    original["masked_columns"].append("new_sensitive_column")
    original["future_policy"] = {"deny": ["new-restriction"]}
    release["spec"]["values"]["data"]["json"] = json.dumps(original)
    resources = tmp_path / "opa.yaml"
    resources.write_text(yaml.safe_dump(release))
    operations = json.loads(
        auth.auth_patches(resources)["apps-scout"]["opa"][0]["patch"]
    )
    actual = json.loads(
        next(p["value"] for p in operations if p["path"] == "/spec/values/data/json")
    )
    assert set(actual) == set(original)
    for key in original.keys() - {"filtered_tables", "attribute_filters"}:
        assert actual[key] == original[key]
    assert actual["filtered_tables"] == [
        {"catalog": "delta", "schema": "default", "table": "test_reports"}
    ]
    assert actual["attribute_filters"] == auth.ATTRIBUTE_FILTERS


@pytest.mark.parametrize("copies", [0, 2])
def test_opa_fixture_cannot_silently_select_missing_or_duplicate_release(
    tmp_path, copies
):
    release = yaml.safe_load(auth.OPA_RESOURCES.read_text())
    resources = tmp_path / "opa.yaml"
    resources.write_text(yaml.safe_dump_all([release] * copies))
    with pytest.raises(ValueError, match="one scout-opa"):
        auth.auth_patches(resources)


@pytest.mark.skipif(
    shutil.which("flux") is None,
    reason="Flux CLI required for offline controller render",
)
def test_real_flux_patches_keep_auth_and_dependency_controls(prepared, tmp_path):
    site, roots_path = prepared
    # Use the same full substitution set as the CI job. Both render stages run
    # strictly, so dollars introduced inside nested OPA patches are checked too.
    merged_values = tmp_path / "cluster-vars.values.json"
    with merged_values.open("w") as output:
        subprocess.run(
            [
                "jq",
                "-s",
                ".[0] * .[1] | del(._comment)",
                str(REPO / "tooling/deploy/fixtures/cluster-vars.values.json"),
                str(HERE / "cluster-vars.values.json"),
            ],
            stdout=output,
            check=True,
        )
    generated = subprocess.run(
        [
            sys.executable,
            str(REPO / "tooling/deploy/gen_cluster_vars.py"),
            "--values",
            str(merged_values),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    values = yaml.safe_load(generated.stdout)["data"]
    source = tmp_path / "config"
    shutil.copytree(REPO / "deploy", source)
    rendered = {}

    def build(doc, base, label):
        doc["spec"].setdefault("postBuild", {})["substitute"] = {
            k: str(v) for k, v in values.items()
        }
        file = tmp_path / f"{label}.yaml"
        file.write_text(yaml.safe_dump(doc))
        result = subprocess.run(
            [
                "flux",
                "build",
                "kustomization",
                doc["metadata"]["name"],
                "--path",
                str(base / doc["spec"]["path"]),
                "--kustomization-file",
                str(file),
                "--dry-run",
                "--strict-substitute",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "KUBECONFIG": "/dev/null"},
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return [d for d in yaml.safe_load_all(result.stdout) if d]

    for root in yaml.safe_load_all(roots_path.read_text()):
        for doc in build(root, source, root["metadata"]["name"]):
            if doc["kind"] == "Kustomization":
                rendered[doc["metadata"]["name"]] = doc
    active = {name for name, doc in rendered.items() if not doc["spec"].get("suspend")}
    assert active == set((HERE / "auth-kustomizations.txt").read_text().split())
    for name in active:
        assert {
            d["name"] for d in rendered[name]["spec"].get("dependsOn", [])
        } <= active

    # The CI roots introduce a child's patches array. Fail before a future
    # production policy could be silently replaced by that operation.
    patched = {name for children in auth.auth_patches().values() for name in children}
    for path in (REPO / "deploy/flux", REPO / "deploy/modes/on-prem"):
        for file in path.glob("*.yaml"):
            for doc in yaml.safe_load_all(file.read_text()):
                if (
                    doc
                    and doc.get("kind") == "Kustomization"
                    and doc.get("metadata", {}).get("name") in patched
                ):
                    assert not doc["spec"].get(
                        "patches"
                    ), f"preserve production patches for {doc['metadata']['name']}"

    children = {}
    for child_patches in auth.auth_patches().values():
        for child in child_patches:
            children[child] = build(rendered[child], source, child)

    def resource(child, kind, name):
        return next(
            d
            for d in children[child]
            if d["kind"] == kind and d["metadata"]["name"] == name
        )

    trino = resource("trino-ro", "HelmRelease", "trino")["spec"]["values"]
    assert trino["server"]["workers"] == 1
    assert trino["server"]["config"]["authenticationType"] == "JWT"
    assert trino["server"]["config"]["https"]["enabled"] is True
    assert trino["networkPolicy"]["enabled"] is True
    assert "opa.policy.uri=" in trino["accessControl"]["properties"]
    for name in ("hive-metastore", "hive-metastore-readonly"):
        assert (
            resource("hive-metastore", "HelmRelease", name)["spec"]["values"][
                "resources"
            ]["limits"]["memory"]
            == "512Mi"
        )
    opa = resource("opa", "HelmRelease", "scout-opa")["spec"]["values"]
    assert opa["networkPolicy"]["enabled"] is True
    assert opa["replicaCount"] == 1
    assert (
        json.loads(opa["data"]["json"])["attribute_filters"] == auth.ATTRIBUTE_FILTERS
    )
    realm = resource("keycloak-realm", "HelmRelease", "keycloak-config-cli")["spec"][
        "values"
    ]
    assert realm["trinoAttributeFilters"] == auth.ATTRIBUTE_FILTERS
    assert realm["env"]["KEYCLOAK_URL"].startswith(
        "http://keycloak-service.scout-core:"
    )
    proxy = resource("oauth2-proxy", "HelmRelease", "oauth2-proxy")["spec"]["values"]
    assert proxy["extraArgs"]["provider-ca-file"] == auth.CA_PATH
    assert {v["name"] for v in proxy["extraVolumes"]} == {
        "oauth2-proxy-templates",
        "oauth2-proxy-logo",
        auth.CA_NAME,
    }
    assert (
        'allowed_roles = "oauth2-proxy:oauth2-proxy-user"'
        in proxy["config"]["configFile"]
    )
    assert "insecure_skip_verify" not in proxy["config"]["configFile"]
    forward_auth = resource("edge-on-prem", "Middleware", "oauth2-proxy-auth")["spec"][
        "forwardAuth"
    ]
    assert forward_auth["address"].startswith("https://")
    assert forward_auth["tls"] == {
        "caSecret": auth.CA_NAME,
        "insecureSkipVerify": False,
    }
    launchpad = resource("launchpad", "HelmRelease", "launchpad")["spec"]["values"]
    assert launchpad["env"] == [{"name": "NODE_EXTRA_CA_CERTS", "value": auth.CA_PATH}]
    assert launchpad["auth"]["keycloak"]["enabled"] is True
    assert launchpad["auth"]["keycloak"]["issuer"].startswith("https://")
