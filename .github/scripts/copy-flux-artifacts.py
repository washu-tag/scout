#!/usr/bin/env python3
"""Copy this attempt's tested Scout candidate without rebuilding its OCI content."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling/manifest"))
from producer_plan import IMAGE_PATHS, VENDOR_IMAGES, context  # noqa: E402
from build_haul import parse_predecessor  # noqa: E402


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def load(candidate):
    index = json.loads((candidate / "index.json").read_text())
    expected = context(
        os.environ["GITHUB_REPOSITORY"],
        os.environ["GITHUB_SHA"],
        int(os.environ["GITHUB_RUN_ID"]),
        int(os.environ["GITHUB_RUN_ATTEMPT"]),
        os.environ["VERSION"],
    )
    if any(
        type(index.get(key)) is not type(value) or index.get(key) != value
        for key, value in expected.items()
    ):
        raise ValueError("candidate belongs to another attempt; rerun all jobs")
    for row in index["images"] + index["charts"] + [index["manifest"], index["config"]]:
        if not re.fullmatch(r"ghcr.io/washu-tag/[a-z0-9/-]+", row["repository"]):
            raise ValueError("unexpected candidate repository")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", row["digest"]):
            raise ValueError("invalid candidate digest")
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", row["tag"]):
            raise ValueError("invalid candidate tag")
        layout = row.get("layout")
        if layout and not (candidate / layout).resolve().is_relative_to(
            candidate.resolve()
        ):
            raise ValueError("candidate layout is outside the artifact")
    recorded = {}
    for kind in ("images", "charts"):
        prefix = "ghcr.io/washu-tag/" + ("charts/" if kind == "charts" else "")
        for row in index[kind]:
            if (
                type(row["fresh"]) is not bool
                or row["repository"] != prefix + row["name"]
            ):
                raise ValueError("invalid candidate component identity or freshness")
            if row["repository"] in recorded:
                raise ValueError("duplicate candidate component")
            if row["fresh"] != bool(row["layout"]):
                raise ValueError("fresh components require a canonical layout")
            recorded[row["repository"]] = (row["tag"], row["digest"])
    if recorded != parse_predecessor(str(candidate / "haul.yaml")):
        raise ValueError("candidate inventory differs from the tested haul")
    annotations = {
        "org.opencontainers.image.source": "https://github.com/"
        + expected["repository"],
        "org.opencontainers.image.revision": expected["revision"],
        "org.opencontainers.image.version": expected["version"],
        "io.scout.build.run-id": str(expected["runId"]),
        "io.scout.build.run-attempt": str(expected["runAttempt"]),
    }
    for kind, repository in [
        ("manifest", "scout-manifest"),
        ("config", "scout-config"),
    ]:
        row = index[kind]
        if row["repository"] != "ghcr.io/washu-tag/manifests/" + repository:
            raise ValueError("unexpected candidate metadata repository")
        if row["layout"] != kind or row["tag"] != expected["version"]:
            raise ValueError("unexpected candidate metadata layout or version")
        blob = candidate / row["layout"] / "blobs/sha256" / row["digest"].split(":")[1]
        raw = blob.read_bytes()
        if "sha256:" + hashlib.sha256(raw).hexdigest() != row["digest"]:
            raise ValueError("candidate manifest bytes changed")
        manifest = json.loads(raw)
        wanted = dict(annotations)
        if kind == "config":
            wanted["io.scout.build.manifest-digest"] = index["manifest"]["digest"]
        if any(
            manifest.get("annotations", {}).get(key) != value
            for key, value in wanted.items()
        ):
            raise ValueError("candidate manifest belongs to another build")
        if kind == "manifest":
            layers = {
                layer.get("annotations", {}).get(
                    "org.opencontainers.image.title"
                ): layer
                for layer in manifest["layers"]
            }
            for filename in ("haul.yaml", "haul-upstream.yaml"):
                raw = (candidate / filename).read_bytes()
                if (
                    layers.get(filename, {}).get("digest")
                    != "sha256:" + hashlib.sha256(raw).hexdigest()
                ):
                    raise ValueError("candidate haul differs from its OCI manifest")
    return index


def copy(row, candidate, registry=None):
    args = ["oras", "copy"]
    if row.get("layout"):
        args.append("--from-oci-layout")
        source = str(candidate / row["layout"]) + "@" + row["digest"]
    else:
        source = row["repository"] + "@" + row["digest"]
    repository = row["repository"]
    if registry:
        args.append("--to-plain-http")
        repository = registry + "/" + repository.split("/", 1)[1]
    target = repository + ":" + row["tag"]
    subprocess.run([*args, source, target], check=True)
    descriptor = json.loads(
        run(
            "oras",
            "manifest",
            "fetch",
            "--descriptor",
            *(["--plain-http"] if registry else []),
            target,
        )
    )
    if descriptor["digest"] != row["digest"]:
        raise ValueError("registry copy changed the tested digest")
    return repository + "@" + row["digest"]


def require_release_attempt(index, branch, version):
    """Allow the legacy branch publisher to copy only its still-successful CI attempt."""
    if branch == "main":
        raise ValueError("main images must be published by CI")
    if not version or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("branch release requires the requested release version")
    ci = json.loads(
        run("gh", "api", f"repos/{index['repository']}/actions/runs/{index['runId']}")
    )
    if (
        ci.get("id") != index["runId"]
        or ci.get("run_attempt") != index["runAttempt"]
        or ci.get("head_sha") != index["revision"]
        or ci.get("head_branch") != branch
        or ci.get("repository", {}).get("full_name") != index["repository"]
        or ci.get("head_repository", {}).get("full_name") != index["repository"]
        or ci.get("path") != ".github/workflows/ci.yaml"
        or ci.get("event") not in ("push", "workflow_dispatch")
        or ci.get("status") != "completed"
        or ci.get("conclusion") != "success"
        or str(ci.get("run_number")) != index["version"].rsplit(".", 1)[1]
    ):
        raise ValueError("release CI run, revision or successful attempt changed")
    if {row["name"] for row in index["images"]} != IMAGE_PATHS.keys() or any(
        not row["fresh"]
        or row["tag"] != index["version"]
        or not re.fullmatch(
            r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", row.get("legacyTag") or ""
        )
        for row in index["images"]
    ):
        raise ValueError("branch release requires a complete fresh image candidate")
    if any(
        row["legacyTag"] != version
        for row in index["images"]
        if row["name"] not in VENDOR_IMAGES
    ):
        raise ValueError(
            "candidate image alias differs from the requested release version"
        )


def sign(ref):
    subprocess.run(
        [
            "cosign",
            "sign",
            "--key",
            "env://COSIGN_PRIVATE_KEY",
            "--use-signing-config=false",
            "--tlog-upload=false",
            "--yes",
            ref,
        ],
        check=True,
    )


def patch_roots(path, registry, charts):
    roots = list(yaml.safe_load_all(path.read_text()))
    chart_patches = [
        {
            "target": {
                "kind": "OCIRepository",
                "labelSelector": "scout.xnat.org/chart=" + chart["name"],
            },
            "patch": yaml.safe_dump(
                [
                    {
                        "op": "replace",
                        "path": "/spec/url",
                        "value": "oci://"
                        + registry
                        + "/washu-tag/charts/"
                        + chart["name"],
                    },
                    {"op": "add", "path": "/spec/insecure", "value": True},
                ],
                sort_keys=False,
            ),
        }
        for chart in charts
    ]
    for root in roots:
        directory = ROOT / "deploy" / root["spec"]["path"].removeprefix("./")
        children = {}
        for source in sorted(directory.glob("*.yaml")):
            for child in yaml.safe_load_all(source.read_text()):
                if (
                    child
                    and child.get("apiVersion") == "kustomize.toolkit.fluxcd.io/v1"
                    and child.get("kind") == "Kustomization"
                    and child["spec"].get("sourceRef")
                    == {"kind": "OCIRepository", "name": "scout-config"}
                ):
                    children[child["metadata"]["name"]] = child
        patches = root["spec"].setdefault("patches", [])
        customized = set()
        for patch in patches:
            target = patch.get("target", {})
            names = {
                name
                for name in children
                if re.fullmatch(target.get("name", ".*"), name)
            }
            if target.get("kind") != "Kustomization" or not names:
                continue
            operations = yaml.safe_load(patch["patch"])
            if not isinstance(operations, list):
                continue
            for operation in operations:
                if operation.get("path") == "/spec/patches" and operation.get("op") in (
                    "add",
                    "replace",
                ):
                    operation["value"].extend(chart_patches)
                    customized.update(names)
                    patch["patch"] = yaml.safe_dump(operations, sort_keys=False)
        for name, child in children.items():
            if name not in customized:
                patches.append(
                    {
                        "target": {"kind": "Kustomization", "name": name},
                        "patch": yaml.safe_dump(
                            [
                                {
                                    "op": "add",
                                    "path": "/spec/patches",
                                    "value": [
                                        *child["spec"].get("patches", []),
                                        *chart_patches,
                                    ],
                                }
                            ],
                            sort_keys=False,
                        ),
                    }
                )
    path.write_text(yaml.safe_dump_all(roots, sort_keys=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "validate",
            "test",
            "publish-images",
            "publish-chart",
            "publish-config",
            "patch-roots",
        ],
    )
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--registry")
    parser.add_argument("--chart")
    parser.add_argument("--roots", type=Path)
    parser.add_argument(
        "--release-branch",
        help="Recheck the selected CI attempt before a legacy branch release",
    )
    parser.add_argument("--release-version")
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    index = load(candidate)
    if args.release_branch is not None or args.release_version is not None:
        if args.command != "publish-images":
            parser.error("release-branch is only valid with publish-images")
        if not args.release_branch:
            parser.error("release-version requires release-branch")
        require_release_attempt(index, args.release_branch, args.release_version)
    if args.command == "validate":
        return
    if args.command == "patch-roots":
        if not args.registry or not args.roots:
            parser.error("patch-roots requires --registry and --roots")
        patch_roots(args.roots, args.registry, index["charts"])
        return
    if args.command == "test":
        if not args.registry:
            parser.error("test requires --registry")
        for row in index["images"] + index["charts"] + [index["config"]]:
            copy(row, candidate, args.registry)
    elif args.command == "publish-config":
        if index["manifest"]["digest"] != os.environ["MANIFEST_DIGEST"]:
            raise ValueError("published haul differs from the tested candidate")
        ref = copy(index["config"], candidate)
        sign(ref)
        subprocess.run(["oras", "tag", ref, "main"], check=True)
    else:
        rows = (
            index["images"]
            if args.command == "publish-images"
            else [row for row in index["charts"] if row["name"] == args.chart]
        )
        if args.command == "publish-chart" and len(rows) != 1:
            raise ValueError("chart is absent from candidate")
        for row in rows:
            if not row["fresh"]:
                continue
            ref = copy(row, candidate)
            sign(ref)
            legacy = row.get("legacyTag")
            if legacy:
                # Vendor aliases stay unchanged on a tooling-only rebuild.
                advance = row["name"] not in VENDOR_IMAGES or row.get("publishLegacy")
                if not advance:
                    exists = run(
                        "bash",
                        str(ROOT / ".github/scripts/ghcr-tag-published.sh"),
                        row["name"],
                        legacy,
                    )
                    if exists not in ("true", "false"):
                        raise ValueError("ambiguous legacy tag lookup")
                    advance = exists == "false"
                if advance:
                    subprocess.run(["oras", "tag", ref, legacy], check=True)


if __name__ == "__main__":
    main()
