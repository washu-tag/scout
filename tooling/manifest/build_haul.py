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
import json
import sys
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from haul import render_images
from resolve import resolve_refs

_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)


def ghcr_digest(
    repo: str,
    tag: str,
    *,
    token: Optional[str] = None,
    opener: Callable = urllib.request.urlopen,
) -> Optional[str]:
    """Resolve the current content digest of a ghcr `repo:tag` (the carry source).

    ``repo`` is ``ghcr.io/<path>``; returns the registry's ``Docker-Content-Digest``
    (``sha256:...``) or None if absent. ``opener`` is injectable for tests. Keeping
    this live-inspect I/O here (the glue layer) leaves resolve.py a pure assembler.
    ``token`` is unused today (anonymous per-repo pulls); wire a GITHUB_TOKEN-derived
    bearer through when authed pulls of private packages are needed.
    """
    host, _, path = repo.partition("/")
    if host != "ghcr.io" or not path:
        raise ValueError(f"ghcr_digest expects a ghcr.io/<path> repo, got {repo!r}")
    if token is None:
        with opener(f"https://ghcr.io/token?scope=repository:{path}:pull") as r:
            token = json.load(r).get("token", "")
    req = urllib.request.Request(
        f"https://ghcr.io/v2/{path}/manifests/{tag}",
        method="HEAD",
        headers={"Authorization": f"Bearer {token}", "Accept": _ACCEPT},
    )
    with opener(req) as r:
        return r.headers.get("Docker-Content-Digest")


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
        ref = ref.strip()
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
