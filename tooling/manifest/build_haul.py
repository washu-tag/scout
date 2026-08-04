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
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Callable, Optional
from urllib.error import HTTPError

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
    github_token: Optional[str] = None,
    opener: Callable = urllib.request.urlopen,
) -> Optional[str]:
    """Resolve the current content digest of a ghcr `repo:tag` (the carry source).

    ``repo`` is ``ghcr.io/<path>``; returns the registry's ``Docker-Content-Digest``
    (``sha256:...``) or None if absent. ``opener`` is injectable for tests. Keeping
    this live-inspect I/O here (the glue layer) leaves resolve.py a pure assembler.

    With ``github_token`` (a GITHUB_TOKEN or read:packages PAT) the lookup is
    authenticated, so first-push *private* chart/image packages are readable; GHCR
    accepts the base64-encoded token as the bearer directly. Without it the lookup
    is anonymous via the public scope-token endpoint (public packages only).
    """
    host, _, path = repo.partition("/")
    if host != "ghcr.io" or not path:
        raise ValueError(f"ghcr_digest expects a ghcr.io/<path> repo, got {repo!r}")
    if github_token:
        bearer = base64.b64encode(github_token.encode()).decode()
    else:
        with opener(f"https://ghcr.io/token?scope=repository:{path}:pull") as r:
            bearer = json.load(r).get("token", "")
    req = urllib.request.Request(
        f"https://ghcr.io/v2/{path}/manifests/{tag}",
        method="HEAD",
        headers={"Authorization": f"Bearer {bearer}", "Accept": _ACCEPT},
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
    # Authenticated carry when a token is present (private first-push packages,
    # e.g. not-yet-public charts); anonymous otherwise. The workflow passes it
    # as GITHUB_TOKEN on the render step.
    gh_token = os.environ.get("GITHUB_TOKEN") or None

    def carry(repo: str):
        # A missing carry tag (404) becomes None so resolve_refs fails closed with
        # a clear "neither rebuilt nor carried" naming the repo; any other registry
        # error is surfaced with context instead of a raw urllib traceback.
        try:
            digest = ghcr_digest(repo, args.carry_tag, github_token=gh_token)
        except HTTPError as e:
            if e.code == 404:
                return None
            raise RuntimeError(
                f"registry error carrying {repo}:{args.carry_tag}: {e}"
            ) from e
        return (args.carry_tag, digest) if digest else None

    refs = resolve_refs(components, fresh=fresh, carry=carry)
    sys.stdout.write(render_images(refs, name=args.name))


if __name__ == "__main__":
    main()
