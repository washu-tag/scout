#!/usr/bin/env python3
"""Build the Hauler content manifest for a Scout build (ADR 0033).

Reads the fresh push digests the docker-push action captured (one file per
rebuilt component, format ``<name> <ref>@<digest>``), carries every unchanged
component at the digest it had in the previous build's haul manifest, and
renders the ``kind: Images`` manifest that ``hauler store sync`` consumes.

Carry policy (ADR 0033, resolved by prove-out): carry from the PREDECESSOR haul,
not a live tag lookup. A fixed carry tag is impossible because vendored-upstream
images have no ``:latest`` (superset, keycloak) and superset has no build-lane
tag either; the last published haul records every component's digest, so it is
the tag-agnostic carry source. The workflow oras-pulls it to ``--predecessor``.
The first build has no predecessor, so it must be a full rebuild (every
component fresh); after that each build carries the unchanged rest.

Dependency-free (stdlib only) so it runs on a CI runner with no install step.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from haul import render_images
from resolve import resolve_refs


def _split_ref(ref: str) -> Optional[tuple]:
    """``(repo, (tag, digest))`` for a digest-pinned ``repo:tag@digest``, else None.

    Repos never contain ``:`` (registry host is ``ghcr.io``, no port), so the
    first ``:`` ends the repo and ``@`` splits tag from digest.
    """
    repo, _, rest = ref.partition(":")
    tag, _, digest = rest.partition("@")
    if repo and tag and digest.startswith("sha256:"):
        return repo, (tag, digest)
    return None


def parse_fresh(digests_dir: str) -> dict:
    """Map ``repo -> (tag, digest)`` from docker-push artifact files.

    Each file holds one line ``<name> <repo>:<tag>@<digest>``.
    """
    fresh: dict = {}
    for f in sorted(Path(digests_dir).glob("**/*")):
        if not f.is_file():
            continue
        line = f.read_text().strip()
        if not line:
            continue
        _, _, ref = line.partition(" ")
        parsed = _split_ref(ref.strip())
        if parsed is None:
            raise ValueError(f"unparseable digest line in {f}: {line!r}")
        repo, td = parsed
        fresh[repo] = td
    return fresh


def parse_predecessor(path: str) -> dict:
    """Map ``repo -> (tag, digest)`` from the previous build's haul manifest.

    ``path`` points at the ``kind: Images`` YAML ``render_images`` emits (the
    workflow oras-pulls it). A missing or empty file (the first build, no
    predecessor) yields ``{}``.
    """
    if not path:
        return {}
    p = Path(path)
    if not p.exists() or not p.read_text().strip():
        return {}
    out: dict = {}
    for line in p.read_text().splitlines():
        s = line.strip()
        if not s.startswith("- name:"):
            continue
        ref = s.split(":", 1)[1].strip()
        parsed = _split_ref(ref)
        if parsed is None:
            raise ValueError(f"unparseable predecessor ref: {ref!r}")
        repo, td = parsed
        out[repo] = td
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--digests-dir", required=True, help="dir of docker-push digest files"
    )
    ap.add_argument("--components", required=True, help="file with one repo per line")
    ap.add_argument(
        "--predecessor",
        default="",
        help="previous build's haul manifest (carry source; empty on the first build)",
    )
    ap.add_argument("--name", default="scout")
    ap.add_argument(
        "--check",
        action="store_true",
        help="print 'ready'/'incomplete' and exit 0 instead of rendering; a preflight "
        "for whether every component is carryable yet (predecessor seeded), so the "
        "workflow can SKIP (not fail closed) before the one-time chart seed has run",
    )
    args = ap.parse_args(argv)

    components = [
        ln.strip()
        for ln in Path(args.components).read_text().splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    fresh = parse_fresh(args.digests_dir)
    carried = parse_predecessor(args.predecessor)

    def carry(repo: str):
        # Carry the digest from the predecessor haul. None -> resolve_refs fails
        # closed with "neither rebuilt nor carried", so a component absent from
        # both a fresh push and the last haul (e.g. a brand-new one) blocks the
        # build until it has been published once.
        return carried.get(repo)

    if args.check:
        # Only the carryability question, distinct from render's other validations
        # (a genuine drift still fails loudly there): are all components fresh or carried?
        missing = [r for r in components if r not in fresh and carry(r) is None]
        if missing:
            sys.stderr.write("not yet carryable: " + " ".join(missing) + "\n")
            print("incomplete")
        else:
            print("ready")
        return

    refs = resolve_refs(components, fresh=fresh, carry=carry)
    sys.stdout.write(render_images(refs, name=args.name))


if __name__ == "__main__":
    main()
