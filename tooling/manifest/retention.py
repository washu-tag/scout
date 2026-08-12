#!/usr/bin/env python3
"""Retention policy for the build-lane haul packages (ADR 0033).

Every main build republishes the whole ~5GB platform to the two GHCR packages
`manifests/scout` (the .tar.zst bundle) and `manifests/scout-manifest` (the
manifest), tagged `0.YYYYMMDD.<run>` with `:main` moved onto the newest. Left
unbounded they accumulate ~5GB per build. This computes which package *versions*
to keep vs delete; the workflow feeds it `GET .../versions` and acts on the split.

Policy (deliberately conservative -- deleting the wrong version breaks Flux
consumers that pin by digest, so we under-prune rather than over-prune):
  - KEEP any version tagged `main` (the pointer the next build carries from).
  - KEEP the newest `keep_n` version-tagged artifacts (by created_at).
  - KEEP a cosign signature (`sha256-<hex>.sig`) whose subject digest is a kept
    artifact; DELETE it only when its subject artifact is itself being deleted.
  - KEEP every untagged version (referrer-style signatures, or an in-flight push);
    never auto-delete something we can't positively identify.
  - DELETE the remainder: version-tagged artifacts past the newest `keep_n` (and
    not `main`), plus the signatures of those deleted artifacts.

This module is import-only pure logic (unit-tested offline); the GHCR calls and
the two hard-coded package names live in the workflow so a bug here can never
reach a package it wasn't handed.
"""
from __future__ import annotations

import re

_SIG_TAG = re.compile(r"^sha256-([0-9a-f]{64})\.sig$")


def _sig_subject(tags):
    """Return the subject digest (sha256:<hex>) a cosign `.sig` tag refers to, else None."""
    for t in tags:
        m = _SIG_TAG.match(t)
        if m:
            return "sha256:" + m.group(1)
    return None


def partition(versions, keep_n):
    """Split GHCR package versions into (keep_ids, delete_ids).

    versions: iterable of dicts with keys `id` (int), `created_at` (ISO str),
    `tags` (list[str]), `digest` (str, the version's own sha256:...). keep_n: how
    many version-tagged artifacts to retain for rollback (must be >= 1).
    """
    if keep_n < 1:
        raise ValueError("keep_n must be >= 1 (always keep at least the newest haul)")

    keep, delete = set(), set()
    main_ids, artifacts, sigs = set(), [], []
    digest_of = {}

    for v in versions:
        vid = v["id"]
        tags = v.get("tags") or []
        digest_of[vid] = v.get("digest")
        if not tags:
            keep.add(vid)  # untagged: conservative keep
            continue
        if "main" in tags:
            main_ids.add(vid)
            keep.add(vid)
        subject = _sig_subject(tags)
        if subject is not None:
            sigs.append((vid, subject))
        else:
            artifacts.append(v)

    # Newest keep_n version-tagged artifacts (plus any :main-tagged) are retained.
    artifacts.sort(key=lambda v: (v.get("created_at") or "", v["id"]), reverse=True)
    kept_digests = set()
    for i, v in enumerate(artifacts):
        if v["id"] in main_ids or i < keep_n:
            keep.add(v["id"])
            if v.get("digest"):
                kept_digests.add(v["digest"])
        else:
            delete.add(v["id"])

    deleted_digests = {digest_of[i] for i in delete if digest_of.get(i)}
    for vid, subject in sigs:
        if subject in kept_digests:
            keep.add(vid)
        elif subject in deleted_digests:
            delete.add(vid)  # orphaned by deleting its bundle -> clean it up
        else:
            keep.add(vid)  # subject unknown/absent: conservative keep
    return keep, delete


def _main():
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="Compute haul package retention plan.")
    ap.add_argument(
        "--keep", type=int, default=10, help="version-tagged hauls to retain"
    )
    ap.add_argument(
        "versions_json", help="file of `GET .../versions` JSON, or - for stdin"
    )
    args = ap.parse_args()

    raw = (
        sys.stdin.read()
        if args.versions_json == "-"
        else open(args.versions_json).read()
    )
    data = json.loads(raw)
    versions = [
        {
            "id": v["id"],
            "created_at": v.get("created_at", ""),
            "digest": v.get("name", ""),  # GHCR container version `name` == its digest
            "tags": ((v.get("metadata") or {}).get("container") or {}).get("tags")
            or [],
        }
        for v in data
    ]
    keep, delete = partition(versions, args.keep)

    by_id = {v["id"]: v for v in versions}
    print(
        f"total={len(versions)} keep={len(keep)} delete={len(delete)} (keep_n={args.keep})",
        file=sys.stderr,
    )
    for vid in sorted(delete):
        print(
            f"  DELETE {vid} tags={by_id[vid]['tags'] or '(untagged)'}", file=sys.stderr
        )
    # stdout = machine-readable delete ids, one per line, for the workflow to act on
    for vid in sorted(delete):
        print(vid)


if __name__ == "__main__":
    _main()
