#!/usr/bin/env python3
"""Stamp the Phase 3 ``deploy/`` tree from a build-lane haul manifest (ADR 0031).

The GitOps ``deploy/`` bases carry LITERAL placeholders in git so a plain
``kustomize build`` renders offline: charts pin ``version: '0.0.0'``, Scout images
carry ``tag: latest``, the realm import carries ``config-hash: '0'``. At
config-artifact publish this tool rewrites a COPY of the tree, pinning each
placeholder to the concrete tag in that build's Hauler ``kind: Images`` manifest
(same file ``tooling/manifest/haul.py`` renders).

Rewrites (``--deploy-dir`` in place; must be a COPY):
  * HelmRelease ``version: '0.0.0'`` -> haul tag for the chart at
    ``.spec.chart.spec.chart``.
  * Scout ``values.image`` (washu ``repository:`` + following ``tag:``) -> haul
    tag. Covers hl7log-extractor, hl7-transformer, both superset layers.
  * Inline ``image: ghcr.io/washu-tag/<image>:<tag>`` -> haul tag. Covers the
    keycloak CR image AND the hl7-transformer initContainer (the SECOND
    hl7-transformer literal -- both move in lockstep).
  * keycloak-config-cli ``config-hash: '0'`` -> 8-char truncated sha256 of the
    ``--realm-file`` content, mirroring the Ansible keycloak role
    (roles/keycloak/tasks/configure.yaml ``hash('sha256') | truncate(8,True,'')``).
    With no ``--realm-file`` the hash is of empty bytes and a warning is printed.

Fail closed: a placeholder absent from the haul is a hard error (``StampError``);
after stamping ``verify_clean`` asserts no ``version: '0.0.0'``, no washu image at
``latest``/``0.0.0``, no ``config-hash: '0'`` remain. Dependency-free (stdlib only).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import namedtuple
from pathlib import Path

WASHU = "ghcr.io/washu-tag/"
CHARTS_PREFIX = WASHU + "charts/"
CONFIG_HASH_TRUNC = 8  # matches the Ansible keycloak role's truncate(8, True, "")

Stamp = namedtuple("Stamp", "kind name tag file line")


class StampError(Exception):
    """A placeholder could not be resolved against the haul (fail closed)."""


# haul parsing (self-contained; mirrors build_haul._split_ref)


def _split_ref(ref: str):
    """``(repo, tag, digest)`` for a digest-pinned ``repo:tag@digest``, else None."""
    repo, _, rest = ref.partition(":")
    tag, _, digest = rest.partition("@")
    if repo and tag and digest.startswith("sha256:"):
        return repo, tag, digest
    return None


def parse_haul(path) -> tuple:
    """Parse a Hauler ``kind: Images`` manifest into (images, charts).

    Each maps ``<name> -> tag`` where ``<name>`` is the final repo path segment.
    Images are ``ghcr.io/washu-tag/<name>``; charts are
    ``ghcr.io/washu-tag/charts/<name>``. Non-washu refs are ignored.
    """
    images: dict = {}
    charts: dict = {}
    for line in Path(path).read_text().splitlines():
        s = line.strip()
        if not s.startswith("- name:"):
            continue
        ref = s.split(":", 1)[1].strip()
        parsed = _split_ref(ref)
        if parsed is None:
            raise StampError("unparseable haul ref: {!r}".format(ref))
        repo, tag, _ = parsed
        if repo.startswith(CHARTS_PREFIX):
            charts[repo[len(CHARTS_PREFIX) :]] = tag
        elif repo.startswith(WASHU):
            images[repo[len(WASHU) :]] = tag
    if not images and not charts:
        raise StampError("no ghcr.io/washu-tag refs in haul: {}".format(path))
    return images, charts


# line rewrite rules

_RE_CHART = re.compile(r"^(\s*)chart:\s+(\S+)\s*$")
_RE_VERSION0 = re.compile(r"^(\s*)version:\s*(['\"]?)0\.0\.0\2\s*$")
_RE_IMAGE_INLINE = re.compile(
    r"^(\s*)(-\s+)?image:\s*ghcr\.io/washu-tag/([^:\s/]+):(\S+)\s*$"
)
_RE_REPO = re.compile(r"^(\s*)repository:\s*ghcr\.io/washu-tag/([^:\s/]+)\s*$")
_RE_TAG = re.compile(r"^(\s*)tag:\s*(['\"]?)([^'\"\s]+)\2\s*$")
_RE_CONFIG_HASH0 = re.compile(r"^(\s*)config-hash:\s*(['\"]?)0\2\s*$")


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] in "'\"" and s[-1] == s[0]:
        return s[1:-1]
    return s


def stamp_lines(lines: list, images: dict, charts: dict, config_hash: str, rel: str):
    """Rewrite one file's lines in place. Returns (new_lines, [Stamp]).

    Raises StampError if a placeholder references a component absent from the haul.
    """
    out = []
    stamps = []
    pending_chart = None  # last `chart:` seen, for the next version: 0.0.0
    pending_repo = None  # (name, indent) of a washu repo awaiting its tag:
    for n, line in enumerate(lines, 1):
        # chart name capture (no rewrite)
        m = _RE_CHART.match(line)
        if m:
            pending_chart = _strip_quotes(m.group(2))
            out.append(line)
            continue

        # HelmRelease chart version placeholder
        m = _RE_VERSION0.match(line)
        if m:
            if pending_chart is None:
                raise StampError(
                    "{}:{}: version '0.0.0' with no preceding chart name".format(rel, n)
                )
            if pending_chart not in charts:
                raise StampError(
                    "{}:{}: chart '{}' has no haul component".format(
                        rel, n, pending_chart
                    )
                )
            tag = charts[pending_chart]
            out.append("{}version: '{}'".format(m.group(1), tag))
            stamps.append(Stamp("chart-version", pending_chart, tag, rel, n))
            pending_chart = None
            continue

        # inline Scout image literal (keycloak CR, hl7-transformer initContainer)
        m = _RE_IMAGE_INLINE.match(line)
        if m:
            indent, dash, name = m.group(1), m.group(2) or "", m.group(3)
            if name not in images:
                raise StampError(
                    "{}:{}: image '{}' has no haul component".format(rel, n, name)
                )
            tag = images[name]
            out.append("{}{}image: {}{}:{}".format(indent, dash, WASHU, name, tag))
            stamps.append(Stamp("image-inline", name, tag, rel, n))
            continue

        # values.image repository capture (no rewrite)
        m = _RE_REPO.match(line)
        if m:
            pending_repo = (m.group(2), m.group(1))
            out.append(line)
            continue

        # values.image tag, only when it follows a washu repository at same indent
        m = _RE_TAG.match(line)
        if m and pending_repo is not None and m.group(1) == pending_repo[1]:
            name = pending_repo[0]
            if name not in images:
                raise StampError(
                    "{}:{}: image '{}' has no haul component".format(rel, n, name)
                )
            tag = images[name]
            out.append("{}tag: '{}'".format(m.group(1), tag))
            stamps.append(Stamp("image-values-tag", name, tag, rel, n))
            pending_repo = None
            continue

        # config-hash placeholder
        m = _RE_CONFIG_HASH0.match(line)
        if m:
            out.append("{}config-hash: '{}'".format(m.group(1), config_hash))
            stamps.append(
                Stamp("config-hash", "keycloak-config-cli", config_hash, rel, n)
            )
            continue

        # chart/repo captures are consumed by the very next line (version/tag). Any
        # other real key closes the window, so drop both -- otherwise a later
        # unrelated version/tag (e.g. docker.io keycloak-config-cli tag) mis-stamps.
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            pending_chart = None
            pending_repo = None

        out.append(line)
    return out, stamps


def stamp_tree(deploy_dir, images: dict, charts: dict, config_hash: str) -> list:
    """Rewrite every YAML file under ``deploy_dir`` in place. Returns all stamps."""
    root = Path(deploy_dir)
    stamps = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in (".yaml", ".yml") or not path.is_file():
            continue
        rel = str(path.relative_to(root))
        text = path.read_text()
        lines = text.split("\n")
        new_lines, file_stamps = stamp_lines(lines, images, charts, config_hash, rel)
        if file_stamps:
            path.write_text("\n".join(new_lines))
            stamps.extend(file_stamps)
    return stamps


# fail-closed verification

_V_VERSION0 = re.compile(r"version:\s*['\"]?0\.0\.0")
_V_IMG_BAD = re.compile(r"image:\s*ghcr\.io/washu-tag/[^:\s]+:(latest|0\.0\.0)\b")
_V_CONFIG_HASH0 = re.compile(r"config-hash:\s*['\"]?0['\"]?\s*$")


def verify_clean(deploy_dir) -> list:
    """Scan the stamped tree; return a list of any residual-placeholder violations."""
    root = Path(deploy_dir)
    problems = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in (".yaml", ".yml") or not path.is_file():
            continue
        rel = str(path.relative_to(root))
        pending_repo = None
        for n, line in enumerate(path.read_text().split("\n"), 1):
            if _V_VERSION0.search(line):
                problems.append("{}:{}: chart version still 0.0.0".format(rel, n))
            if _V_IMG_BAD.search(line):
                problems.append(
                    "{}:{}: washu image left at latest/0.0.0".format(rel, n)
                )
            if _V_CONFIG_HASH0.search(line):
                problems.append("{}:{}: config-hash still 0".format(rel, n))
            # washu values.image tag must not be latest/0.0.0
            m = _RE_REPO.match(line)
            if m:
                pending_repo = m.group(1)
                continue
            m = _RE_TAG.match(line)
            if m and pending_repo is not None and m.group(1) == pending_repo:
                if m.group(3) in ("latest", "0.0.0"):
                    problems.append(
                        "{}:{}: washu values.image tag left at {}".format(
                            rel, n, m.group(3)
                        )
                    )
                pending_repo = None
    return problems


def compute_config_hash(realm_file) -> str:
    """8-char truncated sha256 of the realm content (empty bytes if no file)."""
    content = Path(realm_file).read_bytes() if realm_file else b""
    return hashlib.sha256(content).hexdigest()[:CONFIG_HASH_TRUNC]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--deploy-dir", required=True, help="COPY of deploy/ to rewrite in place"
    )
    ap.add_argument("--haul", required=True, help="Hauler kind: Images manifest")
    ap.add_argument(
        "--realm-file", default="", help="realm import content for the config-hash"
    )
    args = ap.parse_args(argv)

    images, charts = parse_haul(args.haul)
    if not args.realm_file:
        sys.stderr.write(
            "warning: no --realm-file; config-hash uses empty-content sha256\n"
        )
    config_hash = compute_config_hash(args.realm_file)

    try:
        stamps = stamp_tree(args.deploy_dir, images, charts, config_hash)
    except StampError as exc:
        sys.stderr.write("stamp failed: {}\n".format(exc))
        raise SystemExit(1)

    problems = verify_clean(args.deploy_dir)
    if problems:
        sys.stderr.write("fail-closed: residual placeholders after stamping:\n")
        for p in problems:
            sys.stderr.write("  " + p + "\n")
        raise SystemExit(1)

    for st in stamps:
        sys.stderr.write(
            "stamped {:<17} {:<22} -> {}  ({}:{})\n".format(
                st.kind, st.name, st.tag, st.file, st.line
            )
        )
    sys.stderr.write("stamped {} placeholder(s)\n".format(len(stamps)))


if __name__ == "__main__":
    main()
