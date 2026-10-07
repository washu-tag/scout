"""Candidate transport keeps tested bytes, attempts and deployment patches intact."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "copy_flux_artifacts", Path(__file__).with_name("copy-flux-artifacts.py")
)
transport = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport)
REPOSITORY = "ghcr.io/washu-tag/"
CONTEXT = {
    "repository": "washu-tag/scout",
    "revision": "a" * 40,
    "runId": 321,
    "runAttempt": 2,
    "version": "0.20261007.123",
}


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def layout(directory, payloads, annotations):
    blobs = directory / "blobs/sha256"
    blobs.mkdir(parents=True)
    layers = []
    for name, raw in payloads.items():
        sha = digest(raw)
        (blobs / sha.removeprefix("sha256:")).write_bytes(raw)
        layers.append(
            {
                "mediaType": "application/yaml",
                "digest": sha,
                "size": len(raw),
                "annotations": {"org.opencontainers.image.title": name},
            }
        )
    empty = b"{}"
    (blobs / digest(empty).removeprefix("sha256:")).write_bytes(empty)
    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.empty.v1+json",
            "digest": digest(empty),
            "size": len(empty),
        },
        "layers": layers,
        "annotations": annotations,
    }
    raw = json.dumps(manifest, separators=(",", ":")).encode()
    sha = digest(raw)
    (blobs / sha.removeprefix("sha256:")).write_bytes(raw)
    (directory / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    (directory / "index.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "manifests": [
                    {
                        "mediaType": manifest["mediaType"],
                        "digest": sha,
                        "size": len(raw),
                        "annotations": {
                            "org.opencontainers.image.ref.name": CONTEXT["version"]
                        },
                    }
                ],
            }
        )
    )
    return sha


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    for name, field in (
        ("GITHUB_REPOSITORY", "repository"),
        ("GITHUB_SHA", "revision"),
        ("GITHUB_RUN_ID", "runId"),
        ("GITHUB_RUN_ATTEMPT", "runAttempt"),
        ("VERSION", "version"),
    ):
        monkeypatch.setenv(name, str(CONTEXT[field]))
    images, charts = [], []
    for repository in (
        (ROOT / "tooling/manifest/components.txt").read_text().splitlines()
    ):
        if not repository or repository.startswith("#"):
            continue
        rows = charts if "/charts/" in repository else images
        rows.append(
            {
                "name": repository.rsplit("/", 1)[1],
                "repository": repository,
                "tag": CONTEXT["version"],
                "digest": digest(repository.encode()),
                "fresh": False,
                "layout": None,
                **(
                    {"legacyTag": None, "publishLegacy": False}
                    if rows is images
                    else {}
                ),
            }
        )
    haul = yaml.safe_dump(
        {
            "apiVersion": "content.hauler.cattle.io/v1",
            "kind": "Images",
            "metadata": {"name": "scout"},
            "spec": {
                "images": [
                    {"name": f"{r['repository']}:{r['tag']}@{r['digest']}"}
                    for r in images + charts
                ]
            },
        },
        sort_keys=False,
    ).encode()
    upstream = (
        b"apiVersion: content.hauler.cattle.io/v1\nkind: Images\nspec:\n  images: []\n"
    )
    (tmp_path / "haul.yaml").write_bytes(haul)
    (tmp_path / "haul-upstream.yaml").write_bytes(upstream)
    annotations = {
        "org.opencontainers.image.source": "https://github.com/"
        + CONTEXT["repository"],
        "org.opencontainers.image.revision": CONTEXT["revision"],
        "org.opencontainers.image.version": CONTEXT["version"],
        "io.scout.build.run-id": str(CONTEXT["runId"]),
        "io.scout.build.run-attempt": str(CONTEXT["runAttempt"]),
    }
    manifest_digest = layout(
        tmp_path / "manifest",
        {"haul.yaml": haul, "haul-upstream.yaml": upstream},
        {**annotations, "io.scout.build.carry-policy": "predecessor-v1"},
    )
    config_digest = layout(
        tmp_path / "config",
        {"scout-config.tar.gz": b"candidate config fixture"},
        {**annotations, "io.scout.build.manifest-digest": manifest_digest},
    )
    index = {
        **CONTEXT,
        "images": images,
        "charts": charts,
        "manifest": {
            "repository": REPOSITORY + "manifests/scout-manifest",
            "tag": CONTEXT["version"],
            "digest": manifest_digest,
            "layout": "manifest",
        },
        "config": {
            "repository": REPOSITORY + "manifests/scout-config",
            "tag": CONTEXT["version"],
            "digest": config_digest,
            "layout": "config",
        },
    }
    (tmp_path / "index.json").write_text(json.dumps(index))
    return tmp_path, index


def save(candidate):
    path, index = candidate
    (path / "index.json").write_text(json.dumps(index))
    return path


def test_complete_candidate_from_this_attempt_is_accepted(candidate):
    path, index = candidate
    assert transport.load(path) == index


@pytest.mark.parametrize("field", CONTEXT)
@pytest.mark.parametrize("change", ["missing", "different", "wrong_type"])
def test_candidate_cannot_borrow_another_attempt_context(candidate, field, change):
    _, index = candidate
    if change == "missing":
        index.pop(field)
    elif change == "wrong_type":
        index[field] = True
    else:
        index[field] = index[field] + 1 if isinstance(index[field], int) else "other"
    with pytest.raises((ValueError, KeyError, TypeError)):
        transport.load(save(candidate))


@pytest.mark.parametrize(
    "field,value",
    [
        ("repository", "ghcr.io/attacker/launchpad"),
        ("repository", "ghcr.io/washu-tag/../../attacker"),
        ("digest", "sha256:" + "A" * 64),
        ("tag", "latest\nother=value"),
        ("layout", "../../elsewhere"),
        ("fresh", "false"),
    ],
)
def test_malformed_candidate_row_fails_before_copy(candidate, field, value):
    _, index = candidate
    index["images"][0][field] = value
    with pytest.raises((ValueError, KeyError, TypeError)):
        transport.load(save(candidate))


@pytest.mark.parametrize(
    "change", ["omit", "duplicate", "different_digest", "different_name"]
)
def test_candidate_copy_inventory_must_equal_tested_haul(candidate, change):
    _, index = candidate
    if change == "omit":
        index["images"].pop()
    elif change == "duplicate":
        index["images"].append(copy.deepcopy(index["images"][0]))
    elif change == "different_digest":
        index["images"][0]["digest"] = "sha256:" + "f" * 64
    else:
        index["images"][0]["name"] = "other-component"
    with pytest.raises(ValueError):
        transport.load(save(candidate))


@pytest.mark.parametrize("field", ["manifest", "config"])
def test_candidate_layout_bytes_must_match_recorded_digest(candidate, field):
    path, index = candidate
    row = index[field]
    blob = path / row["layout"] / "blobs/sha256" / row["digest"].split(":", 1)[1]
    blob.write_bytes(blob.read_bytes() + b"\n")
    with pytest.raises(ValueError):
        transport.load(path)


def change_annotation(candidate, field, key, value):
    path, index = candidate
    row = index[field]
    blob = path / row["layout"] / "blobs/sha256" / row["digest"].split(":", 1)[1]
    manifest = json.loads(blob.read_bytes())
    manifest["annotations"][key] = value
    raw = json.dumps(manifest).encode()
    row["digest"] = digest(raw)
    (blob.parent / row["digest"].split(":", 1)[1]).write_bytes(raw)
    oci_index = path / row["layout"] / "index.json"
    descriptor = json.loads(oci_index.read_text())
    descriptor["manifests"][0].update(digest=row["digest"], size=len(raw))
    oci_index.write_text(json.dumps(descriptor))
    return save(candidate)


@pytest.mark.parametrize("field", ["manifest", "config"])
def test_candidate_layout_context_must_match_run_even_with_valid_digest(
    candidate, field
):
    path = change_annotation(candidate, field, "io.scout.build.run-attempt", "1")
    with pytest.raises(ValueError):
        transport.load(path)


def test_candidate_config_cannot_bind_another_haul(candidate):
    path = change_annotation(
        candidate, "config", "io.scout.build.manifest-digest", "sha256:" + "f" * 64
    )
    with pytest.raises(ValueError, match="another build"):
        transport.load(path)


@pytest.mark.parametrize("filename", ["haul.yaml", "haul-upstream.yaml"])
def test_haul_payload_bytes_must_match_candidate_manifest(candidate, filename):
    path, _ = candidate
    haul = path / filename
    # A byte change that leaves the parsed component inventory intact still must fail.
    haul.write_bytes(haul.read_bytes() + b"# changed after candidate creation\n")
    with pytest.raises(ValueError, match="haul differs"):
        transport.load(path)


def test_different_published_manifest_cannot_copy_or_sign_config(
    candidate, monkeypatch
):
    path, _ = candidate
    monkeypatch.setenv("MANIFEST_DIGEST", "sha256:" + "f" * 64)
    monkeypatch.setattr(
        "sys.argv", ["copy", "publish-config", "--candidate", str(path)]
    )
    effects = []
    monkeypatch.setattr(transport, "copy", lambda *a, **kw: effects.append("copy"))
    monkeypatch.setattr(transport, "sign", lambda *a, **kw: effects.append("sign"))
    with pytest.raises(ValueError, match="published haul differs"):
        transport.main()
    assert effects == []


def kustomize(resources, patches, directory):
    directory.mkdir()
    (directory / "kustomization.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": [str(p) for p in resources],
                "patches": patches,
            },
            sort_keys=False,
        )
    )
    output = subprocess.check_output(
        [
            "kustomize",
            "build",
            "--load-restrictor",
            "LoadRestrictionsNone",
            str(directory),
        ],
        text=True,
    )
    return list(yaml.safe_load_all(output))


@pytest.mark.skipif(not shutil.which("kustomize"), reason="requires kustomize")
@pytest.mark.parametrize("leg", ["ingest", "auth"])
def test_registry_override_preserves_auth_and_production_patches(
    candidate, tmp_path, monkeypatch, leg
):
    path, index = candidate
    sys.path.insert(0, str(ROOT / "tooling/deploy"))
    from stamp_config import parse_haul, stamp_tree

    stamped = tmp_path / "stamped"
    shutil.copytree(ROOT / "deploy", stamped / "deploy")
    images, charts = parse_haul(path / "haul.yaml")
    stamp_tree(stamped / "deploy", images, charts, "a" * 12)
    monkeypatch.setattr(transport, "ROOT", stamped)
    roots_file = (
        ROOT
        / ".github/ci_resources/flux"
        / ("roots-apps-auth.yaml" if leg == "auth" else "roots-apps.yaml")
    )
    roots = list(yaml.safe_load_all(roots_file.read_text()))
    if leg == "auth":
        spec = importlib.util.spec_from_file_location(
            "auth_site", ROOT / ".github/ci_resources/flux/prepare_auth_site.py"
        )
        auth = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(auth)
        for root in roots:
            for child, patches in auth.auth_patches()[root["metadata"]["name"]].items():
                root["spec"]["patches"].append(
                    auth.patch(
                        "Kustomization", child, [auth.add("/spec/patches", patches)]
                    )
                )
    path = tmp_path / "roots.yaml"
    path.write_text(yaml.safe_dump_all(roots))
    transport.patch_roots(path, "localhost:5000", index["charts"])
    modified = list(yaml.safe_load_all(path.read_text()))
    seen_sources = []
    for before, after in zip(roots, modified):
        base = stamped / "deploy" / before["spec"]["path"].removeprefix("./")
        resources = sorted(
            p for p in base.glob("*.yaml") if p.name != "kustomization.yaml"
        )
        key = before["metadata"]["name"]
        original = kustomize(
            resources, before["spec"].get("patches", []), tmp_path / (key + "-before")
        )
        revised = kustomize(
            resources, after["spec"].get("patches", []), tmp_path / (key + "-after")
        )
        by_name = {
            d["metadata"]["name"]: d for d in revised if d["kind"] == "Kustomization"
        }
        for doc in original:
            if doc["kind"] != "Kustomization":
                continue
            new = by_name[doc["metadata"]["name"]]
            for patch in doc["spec"].get("patches", []):
                assert patch in new["spec"].get("patches", []), doc["metadata"]["name"]
            if doc["spec"]["sourceRef"]["kind"] != "OCIRepository":
                assert new["spec"] == doc["spec"]
                continue
            component = stamped / "deploy" / doc["spec"]["path"].removeprefix("./")
            sources = [
                p
                for p in component.rglob("*.yaml")
                if any(
                    d
                    and d.get("kind") == "OCIRepository"
                    and "scout.xnat.org/chart"
                    in d.get("metadata", {}).get("labels", {})
                    for d in yaml.safe_load_all(p.read_text())
                )
            ]
            if not sources:
                continue
            # Exercise actual generated sources with Kustomize; config sources must
            # stay unchanged even when they share the chart sources' component.
            sentinel = tmp_path / ("config-" + doc["metadata"]["name"] + ".yaml")
            sentinel.write_text(
                yaml.safe_dump(
                    {
                        "apiVersion": "source.toolkit.fluxcd.io/v1",
                        "kind": "OCIRepository",
                        "metadata": {
                            "name": "scout-config",
                            "namespace": "flux-system",
                        },
                        "spec": {
                            "url": "oci://ghcr.io/washu-tag/manifests/scout-config",
                            "ref": {"digest": "sha256:" + "f" * 64},
                        },
                    }
                )
            )
            before_sources = kustomize(
                [*sources, sentinel],
                [],
                tmp_path / ("charts-before-" + doc["metadata"]["name"]),
            )
            rendered = kustomize(
                [*sources, sentinel],
                [
                    p
                    for p in new["spec"].get("patches", [])
                    if p.get("target", {}).get("kind") == "OCIRepository"
                ],
                tmp_path / ("charts-" + doc["metadata"]["name"]),
            )
            assert len(before_sources) == len(rendered)
            for original_source, source in zip(before_sources, rendered):
                chart = (
                    source.get("metadata", {})
                    .get("labels", {})
                    .get("scout.xnat.org/chart")
                )
                if source["kind"] == "OCIRepository" and chart:
                    assert (
                        source["spec"]["url"]
                        == "oci://localhost:5000/washu-tag/charts/" + chart
                    )
                    assert source["spec"]["insecure"] is True
                    assert source["spec"]["ref"] == {
                        "digest": charts[chart].split("@", 1)[1]
                    }
                    expected = copy.deepcopy(original_source)
                    expected["spec"].update(url=source["spec"]["url"], insecure=True)
                    assert source == expected
                    seen_sources.append(source)
                else:
                    assert source == original_source
    assert len(seen_sources) >= 5
    assert {s["metadata"]["labels"]["scout.xnat.org/chart"] for s in seen_sources} == {
        "temporal-bootstrap",
        "hl7log-extractor",
        "hl7-transformer",
        "scout-opa",
        "hive-metastore",
        "scout-dashboards",
        "keycloak-config-cli",
        "keycloak-fragment-reconciler",
        "launchpad",
    }


@pytest.mark.skipif(
    os.getenv("SCOUT_RELEASE_REGISTRY_PROOF") != "1",
    reason="opt-in actual OCI registry transport proof",
)
def test_real_registry_copy_preserves_manifest_and_layer_bytes(tmp_path):
    for tool in ("registry", "oras"):
        assert shutil.which(tool), tool + " is required"
    raw = b"binary candidate payload\x00\xff"
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir()
    sha = layout(candidate_dir / "image", {"payload.bin": raw}, {})
    row = {
        "repository": REPOSITORY + "fixture",
        "tag": CONTEXT["version"],
        "digest": sha,
        "layout": "image",
    }
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    host = "localhost:" + str(port)
    with (tmp_path / "registry.log").open("wb") as log:
        process = subprocess.Popen(
            ["registry", "-port", str(port)], stdout=log, stderr=subprocess.STDOUT
        )
        try:
            for _ in range(100):
                try:
                    with urllib.request.urlopen(
                        "http://localhost:" + str(port) + "/v2/", timeout=0.5
                    ):
                        break
                except OSError:
                    assert process.poll() is None
                    time.sleep(0.1)
            else:
                pytest.fail("registry did not start")
            reference = transport.copy(row, candidate_dir, host)
            copied_manifest = subprocess.check_output(
                ["oras", "manifest", "fetch", "--plain-http", reference]
            )
            assert digest(copied_manifest) == sha
            manifest = json.loads(copied_manifest)
            payload = subprocess.check_output(
                [
                    "oras",
                    "blob",
                    "fetch",
                    "--plain-http",
                    "--output",
                    "-",
                    reference.split("@", 1)[0] + "@" + manifest["layers"][0]["digest"],
                ]
            )
            assert payload == raw
        finally:
            process.terminate()
            process.wait(timeout=10)
