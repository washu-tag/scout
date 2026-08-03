#!/usr/bin/env python3
"""Build the Hauler content manifest for a Scout build (ADR 0033).

Reads the fresh push digests the docker-push action captured (one file per
rebuilt component, format ``<name> <ref>@<digest>``), carries every other
component at its current registry digest, and renders the ``kind: Images``
manifest that ``hauler store sync`` consumes.

Carry policy (OPEN sub-decision, ADR 0033): this carries an unchanged component
at its ``--carry-tag`` (default ``latest``) via a live registry inspect. The
alternative is to read the predecessor haul's recorded tag+digest; ``resolve_refs``
is agnostic to the choice, so only ``carry`` below changes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from haul import render_images
from resolve import ghcr_digest, resolve_refs


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
        ref = ref.strip() or line
        repo, _, rest = ref.partition(":")
        tag, _, digest = rest.partition("@")
        if not (repo and tag and digest.startswith("sha256:")):
            raise ValueError(f"unparseable digest line in {f}: {line!r}")
        fresh[repo] = (tag, digest)
    return fresh


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--digests-dir", required=True, help="dir of docker-push digest files"
    )
    ap.add_argument("--components", required=True, help="file with one repo per line")
    ap.add_argument(
        "--carry-tag", default="latest", help="tag to carry unchanged components at"
    )
    ap.add_argument("--name", default="scout")
    args = ap.parse_args(argv)

    components = [
        ln.strip()
        for ln in Path(args.components).read_text().splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    fresh = parse_fresh(args.digests_dir)

    def carry(repo: str):
        digest = ghcr_digest(repo, args.carry_tag)
        return (args.carry_tag, digest) if digest else None

    refs = resolve_refs(components, fresh=fresh, carry=carry)
    sys.stdout.write(render_images(refs, name=args.name))


if __name__ == "__main__":
    main()
