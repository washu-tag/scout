#!/usr/bin/env python3
"""Vendor the Keycloak Operator and its CRDs into the deploy/ base (ADR 0031).

GitOps sites can't fetch raw.githubusercontent.com at deploy time (air-gapped, and a
supply-chain risk), so ``deploy/base/keycloak/operator/upstream/`` carries a copy of
keycloak-k8s-resources' operator manifest and CRDs at ``keycloak_version``
(``ansible/group_vars/all/versions.yaml``). Both lanes apply this copy: Flux through
``deploy/base/keycloak/operator/kustomization.yaml``, Ansible from the repo checkout.

The file set comes from the release's own ``kubernetes/kustomization.yml``, because it
changes between releases (26.7 added the OIDC and SAML client CRDs). This script
downloads exactly those files, removes stale ones, and rewrites the ``resources:`` list in
our kustomization. Upstream's kustomization isn't used directly: it pins
``namespace: keycloak`` and rewrites RoleBinding subjects to it.

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
OPERATOR_DIR = REPO / "deploy/base/keycloak/operator"
SOURCE = "https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/{version}/kubernetes/{name}"


def keycloak_version(versions: Path = VERSIONS) -> str:
    m = re.search(r"^keycloak_version:\s*'?([^\s']+)", versions.read_text(), re.M)
    if not m:
        sys.exit(f"keycloak_version not found in {versions}")
    return m.group(1)


def upstream_resources(kustomization: str) -> list[str]:
    """The ``resources:`` entries of upstream's kustomization.yml (plain file names only)."""
    m = re.search(
        r"^resources:[ \t]*\n((?:[ \t]*(?:-[^\n]*)?\n)*)", kustomization, re.M
    )
    names = re.findall(r"^[ \t]*-[ \t]*(\S+)[ \t]*$", m.group(1), re.M) if m else []
    if not names:
        raise ValueError("no resources in upstream kustomization.yml")
    for name in names:
        if not re.fullmatch(r"[A-Za-z0-9._-]+\.ya?ml", name):
            raise ValueError(
                f"unexpected resource in upstream kustomization.yml: {name!r}"
            )
    return names


def rewrite_resources(kustomization: str, names: list[str]) -> str:
    """Replace our kustomization's ``resources:`` block with ``upstream/<name>`` entries."""
    block = "resources:\n" + "".join(f"  - upstream/{n}\n" for n in names)
    new, count = re.subn(
        r"^resources:[ \t]*\n(?:[ \t]+-[^\n]*\n?)*", block, kustomization, flags=re.M
    )
    if count != 1:
        raise ValueError("expected one resources: block in our kustomization.yaml")
    return new


def _fetch(version: str, name: str) -> bytes:
    with urllib.request.urlopen(
        SOURCE.format(version=version, name=name), timeout=60
    ) as r:
        return r.read()


def vendor(version: str, operator_dir: Path = OPERATOR_DIR) -> list[str]:
    names = upstream_resources(_fetch(version, "kustomization.yml").decode())
    # Fetch every file before writing any, so a failed download leaves the old set intact.
    blobs = {name: _fetch(version, name) for name in names}
    upstream = operator_dir / "upstream"
    for stale in upstream.glob("*.y*ml"):
        if stale.name not in blobs:
            stale.unlink()
    for name, data in blobs.items():
        (upstream / name).write_bytes(data)
    kustomization = operator_dir / "kustomization.yaml"
    kustomization.write_text(rewrite_resources(kustomization.read_text(), names))
    return names


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
    names = vendor(version)
    print(f"vendored keycloak-k8s-resources {version}: {', '.join(names)}")


if __name__ == "__main__":
    main()
