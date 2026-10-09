#!/usr/bin/env python3
"""Freeze and enforce a build's verified carry baseline (ADR 0030 Appendix A).

Registry/signature I/O is in freeze-predecessor.sh. This module validates the
signed manifest metadata, Git ancestry and accumulated paths, then records the
exact inputs that every publishing job must use. No live tag lookup is allowed
after this plan is created. Component paths come from the CI workflow catalog.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import yaml

from build_haul import parse_fresh, parse_predecessor
from haul import render_images
from resolve import resolve_refs

CARRY_POLICY = "predecessor-v1"
REPO = "ghcr.io/washu-tag/"
WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yaml"


def workflow_catalog(workflow: Path) -> tuple[dict, dict]:
    jobs = yaml.safe_load(workflow.read_text())["jobs"]
    filters = yaml.safe_load(
        next(
            step["with"]["filters"]
            for step in jobs["changes"]["steps"]
            if step.get("id") == "filter"
        )
    )
    return jobs, filters


def scopes(filters: dict, name: str) -> tuple[str, ...]:
    patterns = filters.get(name)
    if not isinstance(patterns, list) or not patterns:
        raise ValueError(f"missing component path filter: {name}")
    result = []
    for pattern in patterns:
        if not isinstance(pattern, str):
            raise ValueError(f"unsupported component path filter: {name}")
        scope = pattern[:-2] if pattern.endswith("/**") else pattern
        if not scope or re.search(r"[*?!\[\]{}()]", scope):
            raise ValueError(f"unsupported component path pattern: {pattern}")
        result.append(scope)
    return tuple(result)


def catalog(workflow: Path = WORKFLOW) -> tuple[dict, dict, dict]:
    """Read component and shared build paths from the existing CI catalog."""
    jobs, filters = workflow_catalog(workflow)
    images, charts, image_charts = {}, {}, {}
    for item in jobs["build-and-upload"]["strategy"]["matrix"]["include"]:
        name = item["image-name"]
        if name in images:
            raise ValueError(f"duplicate image component: {name}")
        images[name] = scopes(filters, name)
    for item in jobs["publish-charts"]["strategy"]["matrix"]["include"]:
        name = item["chart-name"]
        if name in charts:
            raise ValueError(f"duplicate chart component: {name}")
        if item["changed"] != name + "-chart":
            raise ValueError(f"unexpected chart path filter: {name}")
        charts[name] = scopes(filters, item["changed"])
        if image := item.get("image-name"):
            if image not in images or image in image_charts:
                raise ValueError(f"unknown or multiply mapped chart image: {image}")
            image_charts[image] = name
    if not images or not charts:
        raise ValueError("component catalog must include images and charts")
    return images, charts, image_charts


IMAGE_PATHS, CHART_PATHS, IMAGE_CHARTS = catalog()
_FILTERS = workflow_catalog(WORKFLOW)[1]
IMAGE_BUILD_PATHS = scopes(_FILTERS, "image_build")
CHART_BUILD_PATHS = scopes(_FILTERS, "chart_build")
CONFIG_PATHS = scopes(_FILTERS, "config")


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str, cwd: Path) -> bytes:
    return subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.PIPE)


def matches(path: str, scopes: tuple[str, ...]) -> bool:
    return any(
        path.startswith(scope) if scope.endswith("/") else path == scope
        for scope in scopes
    )


def classify(paths: list[str], full: bool = False) -> tuple[dict[str, bool], bool]:
    flags = {
        name: full or any(matches(p, scopes + IMAGE_BUILD_PATHS) for p in paths)
        for name, scopes in IMAGE_PATHS.items()
    }
    flags.update(
        {
            name + "-chart": full
            or any(matches(p, scopes + CHART_BUILD_PATHS) for p in paths)
            for name, scopes in CHART_PATHS.items()
        }
    )
    for image, chart in IMAGE_CHARTS.items():
        flags[chart + "-chart"] |= flags[image]
    publish = (
        full or any(flags.values()) or any(matches(p, CONFIG_PATHS) for p in paths)
    )
    return flags, publish


def context(
    repository: str, revision: str, run_id: int, run_attempt: int, version: str
) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("invalid repository")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("revision must be a full lowercase Git SHA")
    if run_id < 1 or run_attempt < 1:
        raise ValueError("run id and attempt must be positive")
    if not re.fullmatch(r"0\.[0-9]{8}\.[1-9][0-9]*", version):
        raise ValueError("invalid build version")
    return dict(
        repository=repository,
        revision=revision,
        runId=run_id,
        runAttempt=run_attempt,
        version=version,
    )


def legacy_manifest(manifest: dict) -> bool:
    """Recognize pre-provenance OCI metadata without trusting its component data."""
    if not isinstance(manifest, dict):
        raise ValueError("predecessor manifest must be an object")
    annotations = manifest.get("annotations", {})
    if not isinstance(annotations, dict):
        raise ValueError("predecessor annotations must be an object")
    if any(
        key
        in (
            "org.opencontainers.image.source",
            "org.opencontainers.image.revision",
            "org.opencontainers.image.version",
        )
        or key.startswith("io.scout.build.")
        for key in annotations
    ):
        return False
    if (
        manifest.get("schemaVersion") != 2
        or manifest.get("mediaType") != "application/vnd.oci.image.manifest.v1+json"
        or not isinstance(manifest.get("layers"), list)
    ):
        raise ValueError("unrecognized predecessor manifest without provenance")
    return True


def create(
    snapshot: Path,
    checkout: Path,
    expected: dict,
    force: bool = False,
    predecessor_repository: str | None = None,
    candidate_only: bool = False,
) -> dict:
    source_repository = predecessor_repository or expected["repository"]
    if source_repository not in (expected["repository"], "washu-tag/scout"):
        raise ValueError(
            "predecessor source repository must be this repository or upstream"
        )
    manifest = json.loads((snapshot / "manifest.json").read_bytes())
    bootstrap = legacy_manifest(manifest)
    predecessor, paths, carry = None, [], False
    if bootstrap:
        if (snapshot / "haul.yaml").read_bytes():
            raise ValueError(
                "legacy predecessor must have an empty haul; never carry unsigned data"
            )
    else:
        annotations = manifest.get("annotations", {})
        if (
            annotations.get("org.opencontainers.image.source")
            != "https://github.com/" + source_repository
        ):
            raise ValueError("predecessor source repository mismatch")
        predecessor = annotations.get("org.opencontainers.image.revision", "")
        if not re.fullmatch(r"[0-9a-f]{40}", predecessor):
            raise ValueError("predecessor has no valid source revision")
        # Main publication requires ancestry. Candidate-only branches may instead
        # rebuild everything when main has advanced beyond their history. A missing
        # commit or Git error is not evidence of divergence and still fails closed.
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", predecessor, expected["revision"]],
            cwd=checkout,
            capture_output=True,
        )
        if result.returncode not in (0, 1):
            raise ValueError("cannot establish predecessor ancestry")
        if result.returncode == 1 and not candidate_only:
            raise ValueError(
                "predecessor is not an ancestor of this checkout; refusing stale/divergent publish"
            )
        # A divergent candidate rebuilds everything, but only branch-local vendor
        # changes may advance existing legacy aliases. Main-only changes do not.
        baseline = (
            git("merge-base", predecessor, expected["revision"], cwd=checkout)
            .decode()
            .strip()
            if result.returncode == 1
            else predecessor
        )
        paths = [
            p.decode()
            for p in git(
                "diff",
                "--name-only",
                "--no-renames",
                "-z",
                baseline,
                expected["revision"],
                cwd=checkout,
            ).split(b"\0")
            if p
        ]
        carry = (
            result.returncode == 0
            and annotations.get("io.scout.build.carry-policy") == CARRY_POLICY
            and not force
        )
    flags, publish = classify(paths, full=not carry)
    # Missing newly introduced components must be built, even when their directory
    # did not change in this diff. Inventory changes normally trigger full rebuild.
    previous = parse_predecessor(str(snapshot / "haul.yaml")) if carry else {}
    for name in IMAGE_PATHS:
        flags[name] |= REPO + name not in previous
    for name in CHART_PATHS:
        flags[name + "-chart"] |= REPO + "charts/" + name not in previous
    for image, chart in IMAGE_CHARTS.items():
        flags[chart + "-chart"] |= flags[image]
    # A failed config publish may already have advanced the complete haul. An
    # equal-revision rerun must still produce this attempt's config and receipt.
    publish |= any(flags.values()) or expected["runAttempt"] > 1
    required = [REPO + name for name in IMAGE_PATHS if flags[name]]
    required += [
        REPO + "charts/" + name for name in CHART_PATHS if flags[name + "-chart"]
    ]
    plan = dict(
        schemaVersion=1,
        **expected,
        predecessorDigest=sha256(snapshot / "manifest.json"),
        predecessorRevision=predecessor,
        predecessorRepository=source_repository,
        predecessorLegacy=bootstrap,
        predecessorHaulDigest=sha256(snapshot / "haul.yaml"),
        carry=carry,
        changedPaths=paths,
        requiredFresh=required,
        flags=flags,
        legacy={
            name + "-legacy": any(matches(p, IMAGE_PATHS[name]) for p in paths)
            for name in ("superset", "keycloak")
        },
        publish=publish,
    )
    (snapshot / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    return plan


def validate(snapshot: Path, expected: dict) -> dict:
    plan = json.loads((snapshot / "plan.json").read_text())
    if plan.get("schemaVersion") != 1:
        raise ValueError("unknown producer plan schema")
    for key, value in expected.items():
        if plan.get(key) != value:
            raise ValueError(
                f"producer plan {key} mismatch; rerun all jobs for this attempt"
            )
    if plan.get("predecessorDigest") != sha256(snapshot / "manifest.json") or plan.get(
        "predecessorHaulDigest"
    ) != sha256(snapshot / "haul.yaml"):
        raise ValueError("frozen predecessor content changed")
    return plan


def render(snapshot: Path, expected: dict, digests: Path, components: Path) -> str:
    plan = validate(snapshot, expected)
    fresh = parse_fresh(str(digests))
    missing = set(plan["requiredFresh"]) - fresh.keys()
    if missing:
        raise ValueError(
            "planned fresh components missing (cannot carry): "
            + ", ".join(sorted(missing))
        )
    unexpected = fresh.keys() - set(plan["requiredFresh"])
    if unexpected:
        raise ValueError("unplanned fresh components: " + ", ".join(sorted(unexpected)))
    for repo, (tag, _) in fresh.items():
        if tag != plan["version"]:
            raise ValueError(f"fresh component {repo} has another build version")
    carried = parse_predecessor(str(snapshot / "haul.yaml")) if plan["carry"] else {}
    repos = [
        p.strip()
        for p in components.read_text().splitlines()
        if p.strip() and not p.lstrip().startswith("#")
    ]
    return render_images(
        resolve_refs(repos, fresh=fresh, carry=carried.get), name="scout"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "validate", "render"))
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--checkout", type=Path, default=Path("."))
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY"),
        required="GITHUB_REPOSITORY" not in os.environ,
    )
    parser.add_argument(
        "--revision",
        default=os.environ.get("GITHUB_SHA"),
        required="GITHUB_SHA" not in os.environ,
    )
    parser.add_argument(
        "--run-id",
        type=int,
        default=os.environ.get("GITHUB_RUN_ID"),
        required="GITHUB_RUN_ID" not in os.environ,
    )
    parser.add_argument(
        "--run-attempt",
        type=int,
        default=os.environ.get("GITHUB_RUN_ATTEMPT"),
        required="GITHUB_RUN_ATTEMPT" not in os.environ,
    )
    parser.add_argument(
        "--version",
        default=os.environ.get("VERSION"),
        required="VERSION" not in os.environ,
    )
    parser.add_argument(
        "--predecessor-repository",
        help="Signed predecessor source; forks may use the upstream washu-tag/scout build",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--candidate-only",
        action="store_true",
        help="Rebuild a non-ancestor baseline instead of carrying; never for main publication",
    )
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--digests-dir", type=Path)
    parser.add_argument(
        "--components", type=Path, default=Path("tooling/manifest/components.txt")
    )
    args = parser.parse_args()
    if (
        args.candidate_only
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_EVENT_NAME") != "pull_request"
    ):
        parser.error("candidate-only planning cannot publish main")
    expected = context(
        args.repository, args.revision, args.run_id, args.run_attempt, args.version
    )
    if args.command == "render":
        if args.digests_dir is None:
            parser.error("render requires --digests-dir")
        print(
            render(args.snapshot, expected, args.digests_dir, args.components), end=""
        )
    else:
        plan = (
            create(
                args.snapshot,
                args.checkout,
                expected,
                args.force,
                args.predecessor_repository,
                args.candidate_only,
            )
            if args.command == "create"
            else validate(args.snapshot, expected)
        )
        if args.github_output:
            with args.github_output.open("a") as output:
                for key, value in {
                    **plan["flags"],
                    **plan["legacy"],
                    "publish": plan["publish"],
                }.items():
                    output.write(f"{key}={str(value).lower()}\n")


if __name__ == "__main__":
    main()
