#!/usr/bin/env python3
"""Vendor the Keycloak Operator and its CRDs into the deploy/ base (ADR 0031).

GitOps sites can't fetch raw.githubusercontent.com at deploy time (air-gapped, and a
supply-chain risk), so ``deploy/base/keycloak/operator/upstream/`` carries a copy of
keycloak-k8s-resources' operator manifest and CRDs. The copy must match
``keycloak_version`` in ``ansible/group_vars/all/versions.yaml``, which the Ansible lane
fetches from directly.

Renovate runs this as a postUpgradeTask on Keycloak bumps (``renovate.json5``); run it by
hand after changing ``keycloak_version``. ``tooling/versions/check_version_copies.py``
fails CI when the vendored operator and ``keycloak_version`` differ.

Usage: vendor_keycloak_operator.py [VERSION]   (default: keycloak_version)
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VERSIONS = REPO / "ansible/group_vars/all/versions.yaml"
DEST = REPO / "deploy/base/keycloak/operator/upstream"
SOURCE = "https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/{version}/kubernetes/{name}"
FILES = (
    "keycloaks.k8s.keycloak.org-v1.yml",
    "keycloakrealmimports.k8s.keycloak.org-v1.yml",
    "kubernetes.yml",
)


def keycloak_version(versions: Path = VERSIONS) -> str:
    m = re.search(r"^keycloak_version:\s*'?([^\s']+)", versions.read_text(), re.M)
    if not m:
        sys.exit(f"keycloak_version not found in {versions}")
    return m.group(1)


def vendor(version: str, dest: Path = DEST) -> None:
    # Fetch every file before writing any, so a failed download leaves the old set intact.
    blobs = {}
    for name in FILES:
        with urllib.request.urlopen(
            SOURCE.format(version=version, name=name), timeout=60
        ) as r:
            blobs[name] = r.read()
    for name, data in blobs.items():
        (dest / name).write_bytes(data)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "version",
        nargs="?",
        help="Keycloak release (default: keycloak_version in versions.yaml)",
    )
    args = ap.parse_args(argv)
    version = args.version or keycloak_version()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        sys.exit(f"not a Keycloak release version: {version!r}")
    vendor(version)
    print(f"vendored keycloak-k8s-resources {version} into {DEST.relative_to(REPO)}")


if __name__ == "__main__":
    main()
