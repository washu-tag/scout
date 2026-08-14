#!/usr/bin/env python3
"""Stamp the Phase 3 ``deploy/`` tree from a build-lane haul manifest (ADR 0031).

The GitOps ``deploy/`` bases carry LITERAL placeholders in git so a plain
``kustomize build`` renders and CI stays offline: Scout OCI charts pin
``version: '0.0.0'``, Scout images carry ``tag: latest`` (or a concrete upstream
tag), and the keycloak realm import carries ``config-hash: '0'``. At
config-artifact publish, this tool rewrites a COPY of the tree, pinning every
placeholder to the concrete tag recorded in that build's Hauler ``kind: Images``
haul manifest (the same file ``tooling/manifest/haul.py`` renders).

Rewrites (operate on ``--deploy-dir`` in place; that dir must be a COPY):
  * HelmRelease ``.spec.chart.spec.version: '0.0.0'`` -> haul tag for
    ``ghcr.io/washu-tag/charts/<chart>`` (chart from the sibling ``chart:`` key).
  * Scout image ``values.image`` (``repository: ghcr.io/washu-tag/<image>`` +
    sibling ``tag:``) -> the image's haul tag. Covers hl7log-extractor,
    hl7-transformer, and both superset layers.
  * Inline Scout image literals ``image: ghcr.io/washu-tag/<image>:<tag>`` -> the
    image's haul tag. Covers the keycloak CR image AND the hl7-transformer
    init-container image (both move in lockstep).
  * keycloak-config-cli ``config-hash: '0'`` -> the 8-char truncated sha256 of the
    realm content (``--realm-file``), mirroring the Ansible keycloak role
    (``roles/keycloak/tasks/configure.yaml``: ``hash('sha256') |
    truncate(8, True, '')``). With no ``--realm-file`` the hash is of empty bytes
    (a documented placeholder) and a warning is printed.

Images/charts are located by PARSING each doc (yaml node line numbers), not by
matching adjacent lines, so a values block whose keys are reordered (``tag:``
before ``repository:``) is still found -- key order carries no meaning in YAML, so
neither the stamper nor the fail-closed verifier can depend on it. Only the value
line is rewritten, so comments and formatting survive. PyYAML is the one non-stdlib
dependency (already used by validate-deploy.yaml and present via ansible-core for
the tests; this tool only runs in CI).

Fail closed: a placeholder whose component is absent from the haul is a hard error
(``StampError``); and after stamping, ``verify_clean`` re-parses and asserts no
``version: '0.0.0'``, no ``ghcr.io/washu-tag`` image left at ``latest``/``0.0.0``,
and no ``config-hash: '0'`` remain.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import namedtuple
from pathlib import Path

import yaml

WASHU = "ghcr.io/washu-tag/"
CHARTS_PREFIX = WASHU + "charts/"
CONFIG_HASH_TRUNC = 8  # matches the Ansible keycloak role's truncate(8, True, "")
_BAD_TAGS = ("latest", "0.0.0")

Stamp = namedtuple("Stamp", "kind name tag file line")


class StampError(Exception):
    """A placeholder could not be resolved against the haul (fail closed)."""


# --- haul parsing (self-contained; mirrors build_haul._split_ref) -------------


def _split_ref(ref: str):
    """``(repo, tag, digest)`` for a digest-pinned ``repo:tag@digest``, else None."""
    repo, _, rest = ref.partition(":")
    tag, _, digest = rest.partition("@")
    if repo and tag and digest.startswith("sha256:"):
        return repo, tag, digest
    return None


def parse_haul(path) -> tuple:
    """Parse a Hauler ``kind: Images`` manifest into (images, charts).

    Each maps the final repo path segment -> tag. Images are
    ``ghcr.io/washu-tag/<name>``; charts are ``ghcr.io/washu-tag/charts/<name>``.
    Non-washu refs are ignored.
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


# --- value-line rewrite -------------------------------------------------------

# A scalar mapping entry: <indent><key>: <value>[  # comment]. Rewrites only the
# value, preserving indent, key, and any trailing comment.
# The key may be the first entry of a sequence item ("- image: ..."), so allow a
# leading "- "; key order carries no YAML meaning and neither should this.
_ENTRY = re.compile(r"^(\s*(?:- )?[\w.\-/]+:\s+)(.*?)(\s+#.*)?$")


def _rewrite_value(line: str, new_value: str, quote: bool) -> str:
    m = _ENTRY.match(line)
    if not m:
        raise StampError(
            "unexpected value line, cannot stamp safely: {!r}".format(line)
        )
    v = "'{}'".format(new_value) if quote else new_value
    return m.group(1) + v + (m.group(3) or "")


def _inline_image_name(value: str):
    """``<name>`` for a ``ghcr.io/washu-tag/<name>:<tag>`` image literal, else None.

    Excludes chart refs (charts/...) and any ref with an extra path segment.
    """
    if not value.startswith(WASHU) or value.startswith(CHARTS_PREFIX):
        return None
    rest = value[len(WASHU) :]
    if ":" not in rest:
        return None
    name = rest.split(":", 1)[0]
    return name if "/" not in name else None


# --- parse-based placeholder discovery ----------------------------------------


def _scalar(node):
    return node.value if isinstance(node, yaml.ScalarNode) else None


def _find_patches(text: str, images: dict, charts: dict, config_hash: str, rel: str):
    """Parse ``text``; return [(line0, new_line, Stamp)] for every placeholder.

    Order-independent (locates each value node by its line). Raises StampError if a
    washu component referenced by a placeholder is absent from the haul.
    """
    try:
        docs = list(yaml.compose_all(text))
    except yaml.YAMLError as exc:
        # Fail closed: a deploy file we can't parse can't be checked for
        # placeholders, so refuse to publish rather than skip it silently.
        raise StampError("{}: not valid YAML, cannot stamp: {}".format(rel, exc))
    lines = text.split("\n")
    patches: list = []

    def add(node, new_value, quote, kind, name, tag):
        ln = node.start_mark.line
        patches.append(
            (
                ln,
                _rewrite_value(lines[ln], new_value, quote),
                Stamp(kind, name, tag, rel, ln + 1),
            )
        )

    def visit(node):
        if isinstance(node, yaml.MappingNode):
            kv = {k.value: v for k, v in node.value if isinstance(k, yaml.ScalarNode)}
            # Scout OCI chart version placeholder: {chart: <washu chart>, version: '0.0.0'}
            if (
                _scalar(kv.get("chart")) is not None
                and _scalar(kv.get("version")) == "0.0.0"
            ):
                chart = kv["chart"].value
                if chart not in charts:
                    raise StampError(
                        "{}:{}: chart '{}' has no haul component".format(
                            rel, kv["version"].start_mark.line + 1, chart
                        )
                    )
                add(
                    kv["version"],
                    charts[chart],
                    True,
                    "chart-version",
                    chart,
                    charts[chart],
                )
            # values.image: {repository: ghcr.io/washu-tag/<image>, tag: ...}
            repo = _scalar(kv.get("repository"))
            if (
                repo
                and repo.startswith(WASHU)
                and not repo.startswith(CHARTS_PREFIX)
                and "tag" in kv
            ):
                name = repo[len(WASHU) :]
                if name not in images:
                    raise StampError(
                        "{}:{}: image '{}' has no haul component".format(
                            rel, kv["tag"].start_mark.line + 1, name
                        )
                    )
                add(
                    kv["tag"],
                    images[name],
                    True,
                    "image-values-tag",
                    name,
                    images[name],
                )
            # config-hash placeholder
            if _scalar(kv.get("config-hash")) == "0":
                add(
                    kv["config-hash"],
                    config_hash,
                    True,
                    "config-hash",
                    "keycloak-config-cli",
                    config_hash,
                )
            # inline Scout image literal
            iname = _inline_image_name(_scalar(kv.get("image")) or "")
            if iname is not None:
                if iname not in images:
                    raise StampError(
                        "{}:{}: image '{}' has no haul component".format(
                            rel, kv["image"].start_mark.line + 1, iname
                        )
                    )
                add(
                    kv["image"],
                    "{}{}:{}".format(WASHU, iname, images[iname]),
                    False,
                    "image-inline",
                    iname,
                    images[iname],
                )
            for _, v in node.value:
                visit(v)
        elif isinstance(node, yaml.SequenceNode):
            for item in node.value:
                visit(item)

    for d in docs:
        if d is not None:
            visit(d)
    return patches


def stamp_tree(deploy_dir, images: dict, charts: dict, config_hash: str) -> list:
    """Rewrite every YAML file under ``deploy_dir`` in place. Returns all stamps."""
    root = Path(deploy_dir)
    stamps = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in (".yaml", ".yml") or not path.is_file():
            continue
        rel = str(path.relative_to(root))
        text = path.read_text()
        patches = _find_patches(text, images, charts, config_hash, rel)
        if not patches:
            continue
        lines = text.split("\n")
        for line0, new_line, _ in patches:
            lines[line0] = new_line
        path.write_text("\n".join(lines))
        stamps.extend(s for _, _, s in patches)
    return stamps


# --- fail-closed verification (parse-based, order-independent) ----------------


def verify_clean(deploy_dir) -> list:
    """Re-parse the stamped tree; return any residual-placeholder violations."""
    root = Path(deploy_dir)
    problems = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in (".yaml", ".yml") or not path.is_file():
            continue
        rel = str(path.relative_to(root))
        try:
            docs = list(yaml.compose_all(path.read_text()))
        except yaml.YAMLError as exc:
            # Fail closed: an unparseable file is a violation, not a skip.
            problems.append("{}: not valid YAML, cannot verify: {}".format(rel, exc))
            continue

        def visit(node):
            if isinstance(node, yaml.MappingNode):
                kv = {
                    k.value: v for k, v in node.value if isinstance(k, yaml.ScalarNode)
                }
                if (
                    _scalar(kv.get("chart")) is not None
                    and _scalar(kv.get("version")) == "0.0.0"
                ):
                    problems.append(
                        "{}:{}: chart version still 0.0.0".format(
                            rel, kv["version"].start_mark.line + 1
                        )
                    )
                repo = _scalar(kv.get("repository"))
                if (
                    repo
                    and repo.startswith(WASHU)
                    and not repo.startswith(CHARTS_PREFIX)
                ):
                    if _scalar(kv.get("tag")) in _BAD_TAGS:
                        problems.append(
                            "{}:{}: washu values.image tag still {}".format(
                                rel, kv["tag"].start_mark.line + 1, kv["tag"].value
                            )
                        )
                if _scalar(kv.get("config-hash")) == "0":
                    problems.append(
                        "{}:{}: config-hash still 0".format(
                            rel, kv["config-hash"].start_mark.line + 1
                        )
                    )
                iv = _scalar(kv.get("image")) or ""
                if (
                    _inline_image_name(iv) is not None
                    and iv.rsplit(":", 1)[-1] in _BAD_TAGS
                ):
                    problems.append(
                        "{}:{}: washu image left at {}".format(
                            rel, kv["image"].start_mark.line + 1, iv.rsplit(":", 1)[-1]
                        )
                    )
                for _, v in node.value:
                    visit(v)
            elif isinstance(node, yaml.SequenceNode):
                for item in node.value:
                    visit(item)

        for d in docs:
            if d is not None:
                visit(d)
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
