#!/usr/bin/env python3
"""Compose one run's Flux candidate using Docker, Helm and ORAS.

Docker archives are converted once. Tests and publication copy the resulting
OCI layouts; neither rebuilds images nor repackages charts or deployment config.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling/manifest"))
sys.path.insert(0, str(ROOT / "tooling/deploy"))

from build_haul import parse_predecessor  # noqa: E402
from haul import render_images  # noqa: E402
import producer_plan  # noqa: E402
from resolve_upstream import resolve as resolve_upstream  # noqa: E402
from stamp_config import (
    compute_config_hash,
    parse_haul,
    stamp_tree,
    verify_clean,
)  # noqa: E402

REPOSITORY = "ghcr.io/washu-tag/"
MANIFEST_REPO = REPOSITORY + "manifests/scout-manifest"
CONFIG_REPO = REPOSITORY + "manifests/scout-config"


def run(*args, capture=False, cwd=None, env=None):
    result = subprocess.run(
        [str(arg) for arg in args],
        check=True,
        cwd=cwd or ROOT,
        env=env,
        stdout=subprocess.PIPE if capture else None,
        text=True,
    )
    return result.stdout.strip() if capture else None


def catalog():
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yaml").read_text())["jobs"]
    return (
        jobs["build-and-upload"]["strategy"]["matrix"]["include"],
        jobs["publish-charts"]["strategy"]["matrix"]["include"],
    )


def descriptor(reference, *, local=False):
    args = ["oras", "manifest", "fetch", "--descriptor"]
    if local:
        args.append("--plain-http")
    return json.loads(run(*args, reference, capture=True))


def archive_reference(archive: Path, name: str) -> str:
    with tarfile.open(archive) as bundle:
        stream = bundle.extractfile("manifest.json")
        if stream is None:
            raise ValueError(f"{archive} has no Docker manifest")
        manifest = json.load(stream)
    if len(manifest) != 1 or len(manifest[0].get("RepoTags", [])) != 1:
        raise ValueError(f"{archive} must contain one tagged image")
    reference = manifest[0]["RepoTags"][0]
    if reference.rsplit(":", 1)[0] != REPOSITORY + name:
        raise ValueError(f"{archive} contains another image: {reference}")
    return reference


def copy_layout(source, destination: Path, tag, digest, *, local=False):
    args = ["oras", "copy", "--to-oci-layout"]
    if local:
        args.append("--from-plain-http")
    run(*args, source, f"{destination}:{tag}")
    entries = json.loads((destination / "index.json").read_text())["manifests"]
    selected = [
        item
        for item in entries
        if item.get("annotations", {}).get("org.opencontainers.image.ref.name") == tag
    ]
    if len(selected) != 1 or selected[0]["digest"] != digest:
        raise ValueError(f"OCI copy changed the descriptor for {source}")


def prepare_images(matrix, plan, images, output, registry, previous, digests):
    records = []
    for component in matrix:
        name = component["image-name"]
        repo = REPOSITORY + name
        fresh = plan["flags"][name]
        archive = images / f"{name}.tar"
        record = dict(
            name=name,
            repository=repo,
            fresh=fresh,
            layout=None,
            legacyTag=None,
            publishLegacy=plan.get("legacy", {}).get(name + "-legacy", False),
        )
        if fresh:
            if not archive.is_file():
                raise ValueError(
                    f"missing current-attempt image archive: {archive}; rerun all jobs"
                )
            loaded_ref = archive_reference(archive, name)
            tag = plan["version"]
            local_ref = f"{registry}/washu-tag/{name}:{tag}"
            run("docker", "load", "--input", archive)
            run("docker", "tag", loaded_ref, local_ref)
            run("docker", "push", local_ref)
            digest = descriptor(local_ref, local=True)["digest"]
            layout = f"images/{name}"
            copy_layout(
                f"{local_ref}@{digest}", output / layout, tag, digest, local=True
            )
            record.update(layout=layout, legacyTag=loaded_ref.rsplit(":", 1)[1])
            (digests / f"image-{name}.txt").write_text(
                f"{name} {repo}:{tag}@{digest}\n"
            )
            run("docker", "image", "rm", loaded_ref, local_ref)
            archive.unlink()
        else:
            if archive.exists():
                raise ValueError(f"unplanned image archive for {name}")
            tag, digest = previous[repo]
        records.append(dict(record, tag=tag, digest=digest))
    return records


def prepare_charts(
    matrix, plan, predecessor, output, registry, previous, digests, scratch
):
    records = []
    for component in matrix:
        name = component["chart-name"]
        repo = REPOSITORY + "charts/" + name
        fresh = plan["flags"][component["changed"]]
        layout = f"charts/{name}"
        if fresh:
            tag = plan["version"]
            image_name = component.get("image-name", name)
            app_version = run(
                "bash",
                ".github/scripts/chart-app-version.sh",
                name,
                tag,
                predecessor / "haul.yaml",
                capture=True,
                env={
                    **os.environ,
                    "IMAGE_REBUILT": str(plan["flags"].get(image_name, False)).lower(),
                },
            )
            args = [
                "helm",
                "package",
                component["chart-dir"],
                "--version",
                tag,
                "--destination",
                scratch,
            ]
            if app_version:
                args += ["--app-version", app_version]
            run(*args)
            archive = scratch / f"{name}-{tag}.tgz"
            run("bash", ".github/scripts/assert-chart-not-latest.sh", archive, name)
            run(
                "helm",
                "push",
                archive,
                f"oci://{registry}/washu-tag/charts",
                "--plain-http",
            )
            source = f"{registry}/washu-tag/charts/{name}:{tag}"
            digest = descriptor(source, local=True)["digest"]
            copy_layout(f"{source}@{digest}", output / layout, tag, digest, local=True)
            (digests / f"chart-{name}.txt").write_text(
                f"{name} {repo}:{tag}@{digest}\n"
            )
            archive.unlink()
        else:
            tag, digest = previous[repo]
            layout = None
        records.append(
            dict(
                name=name,
                repository=repo,
                tag=tag,
                digest=digest,
                layout=layout,
                fresh=fresh,
            )
        )
    return records


def annotations(context):
    return {
        "org.opencontainers.image.source": "https://github.com/"
        + context["repository"],
        "org.opencontainers.image.revision": context["revision"],
        "org.opencontainers.image.version": context["version"],
        "io.scout.build.run-id": str(context["runId"]),
        "io.scout.build.run-attempt": str(context["runAttempt"]),
    }


def push_files(output, layout, repo, version, files, metadata):
    manifest = output / f"{layout}-descriptor.json"
    args = [
        "oras",
        "push",
        "--oci-layout",
        f"{output / layout}:{version}",
        "--export-manifest",
        manifest,
    ]
    for key, value in metadata.items():
        args += ["--annotation", f"{key}={value}"]
    run(*args, *files, cwd=output)
    digest = "sha256:" + hashlib.sha256(manifest.read_bytes()).hexdigest()
    manifest.unlink()
    return dict(repository=repo, tag=version, digest=digest, layout=layout)


def package_config(deploy: Path, destination: Path):
    with tarfile.open(destination, "w:gz") as archive:
        for name in (
            "base",
            "bootstrap",
            "flux",
            "modes",
            "required-vars.txt",
            "required-secret-values.txt",
        ):
            archive.add(deploy / name, arcname=name)


def prepare(images: Path, predecessor: Path, output: Path, registry: str):
    context = producer_plan.context(
        os.environ["GITHUB_REPOSITORY"],
        os.environ["GITHUB_SHA"],
        int(os.environ["GITHUB_RUN_ID"]),
        int(os.environ["GITHUB_RUN_ATTEMPT"]),
        os.environ["VERSION"],
    )
    plan = producer_plan.validate(predecessor, context)
    previous = (
        parse_predecessor(str(predecessor / "haul.yaml")) if plan["carry"] else {}
    )
    image_matrix, chart_matrix = catalog()
    # Reusing a partial directory could carry bytes from another workflow attempt.
    output.mkdir(parents=True, exist_ok=False)
    (output / "images").mkdir()
    (output / "images/keep.txt").write_text(
        "Layouts are present only for images rebuilt in this attempt.\n"
    )
    with tempfile.TemporaryDirectory(prefix="scout-candidate-") as temp:
        scratch = Path(temp)
        digests = scratch / "digests"
        digests.mkdir()
        records = dict(
            images=prepare_images(
                image_matrix, plan, images, output, registry, previous, digests
            ),
            charts=prepare_charts(
                chart_matrix,
                plan,
                predecessor,
                output,
                registry,
                previous,
                digests,
                scratch,
            ),
        )
        haul = output / "haul.yaml"
        haul.write_text(
            producer_plan.render(
                predecessor, context, digests, ROOT / "tooling/manifest/components.txt"
            )
        )
        upstream = resolve_upstream(
            str(ROOT / "tooling/manifest/upstream-images.txt"),
            str(ROOT / "ansible/group_vars/all/versions.yaml"),
        )
        (output / "haul-upstream.yaml").write_text(
            render_images(
                [f"{ref}@{descriptor(ref)['digest']}" for ref in upstream],
                name="scout-upstream",
            )
        )
        metadata = annotations(context)
        records["manifest"] = push_files(
            output,
            "manifest",
            MANIFEST_REPO,
            context["version"],
            ["haul.yaml:application/yaml", "haul-upstream.yaml:application/yaml"],
            {
                **metadata,
                "io.scout.build.carry-policy": producer_plan.CARRY_POLICY,
                "io.scout.build.predecessor-digest": plan["predecessorDigest"],
            },
        )
        deploy = scratch / "deploy"
        shutil.copytree(ROOT / "deploy", deploy)
        image_refs, chart_refs = parse_haul(haul)
        stamp_tree(
            deploy,
            image_refs,
            chart_refs,
            compute_config_hash(
                ROOT / "helm/keycloak-config-cli/files/scout-realm.json"
            ),
        )
        problems = verify_clean(deploy)
        if problems:
            raise ValueError("\n".join(problems))
        config_archive = output / "scout-config.tar.gz"
        package_config(deploy, config_archive)
        records["config"] = push_files(
            output,
            "config",
            CONFIG_REPO,
            context["version"],
            ["scout-config.tar.gz:application/gzip"],
            {
                **metadata,
                "io.scout.build.manifest-digest": records["manifest"]["digest"],
            },
        )
        config_archive.unlink()
    (output / "index.json").write_text(
        json.dumps(dict(**context, **records), indent=2) + "\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("images", "predecessor", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--registry", required=True)
    args = parser.parse_args()
    prepare(
        args.images.resolve(),
        args.predecessor.resolve(),
        args.output.resolve(),
        args.registry,
    )


if __name__ == "__main__":
    main()
