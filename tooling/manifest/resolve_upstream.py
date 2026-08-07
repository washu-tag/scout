"""Resolve upstream image refs (repo:tag) from Ansible's version source of truth.

The build-lane haul carries the wrapper charts' third-party images (ADR 0033).
Their tags live in ``ansible/group_vars/all/versions.yaml`` (Renovate-tracked, the
deploy-time source of truth), NOT in this repo's chart values, so hardcoding them
here would drift from what Scout deploys. This reads the ``<repo> <version-var>``
map in ``upstream-images.txt`` and prints one ``repo:tag`` per line for the caller
to resolve to ``@digest``.

Stdlib only (like build_haul.py) so it runs on a CI runner with no install step.
``versions.yaml`` is flat ``key: value`` (top-level only), so a line parser is
enough; a stray nested/quoted value can't collide because we only look up the
exact vars named in the map.
"""

from __future__ import annotations

import sys


def load_versions(path: str) -> dict[str, str]:
    """Parse the flat top-level ``key: value`` pairs of versions.yaml."""
    out: dict[str, str] = {}
    for raw in open(path):
        # Skip comments, blanks, and any indented (nested) line.
        if not raw.strip() or raw[0] in "#" or raw[0].isspace():
            continue
        key, sep, val = raw.partition(":")
        if not sep:
            continue
        # Drop trailing inline comment, then surrounding quotes/space.
        val = val.split(" #", 1)[0].strip().strip("'\"")
        out[key.strip()] = val
    return out


def resolve(mapping_path: str, versions_path: str) -> list[str]:
    versions = load_versions(versions_path)
    refs: list[str] = []
    for raw in open(mapping_path):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            raise ValueError(f"expected '<repo> <version-var>', got: {raw!r}")
        repo, var = parts
        if var not in versions:
            raise ValueError(f"{var!r} not found in {versions_path}")
        tag = versions[var]
        if not tag:
            raise ValueError(f"{var!r} is empty in {versions_path}")
        refs.append(f"{repo}:{tag}")
    return refs


if __name__ == "__main__":
    for ref in resolve(sys.argv[1], sys.argv[2]):
        print(ref)
