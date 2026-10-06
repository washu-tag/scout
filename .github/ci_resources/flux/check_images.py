#!/usr/bin/env python3
"""Fail when a running pod's image comes from a registry the staging Harbor doesn't mirror.

An air-gapped site pulls through the Harbor proxy projects listed in
ansible/roles/harbor/defaults/main.yaml (public_registry), so an image from any other
registry fails there long after this proof passed. Prints every image the cluster runs.
"""

import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
HARBOR = REPO / "ansible" / "roles" / "harbor" / "defaults" / "main.yaml"


def registry(image: str) -> str:
    """The registry host of an image reference, Docker Hub when it names none."""
    first, _, rest = image.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        return first
    return "docker.io"


def main() -> None:
    defaults = yaml.safe_load(HARBOR.read_text())
    mirrored = {p["public_registry"] for p in defaults["harbor_registry_proxies"]}
    pods = json.loads(
        subprocess.run(
            ["kubectl", "get", "pods", "-A", "-o", "json"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )["items"]
    images = {}
    for p in pods:
        spec = p["spec"]
        for c in spec.get("initContainers", []) + spec.get("containers", []):
            images.setdefault(c["image"], set()).add(p["metadata"]["namespace"])
    bad = []
    for image in sorted(images):
        reg = registry(image)
        mark = "ok  " if reg in mirrored else "MISS"
        print(
            "  {} {:22} {}  ({})".format(
                mark, reg, image, ", ".join(sorted(images[image]))
            )
        )
        if reg not in mirrored:
            bad.append(image)
    for image in bad:
        print(
            "::error::{} is not on a registry the staging Harbor mirrors".format(image)
        )
    if bad:
        sys.exit(1)
    print(
        "{} images, all from mirrored registries ({})".format(
            len(images), ", ".join(sorted(mirrored))
        )
    )


if __name__ == "__main__":
    main()
