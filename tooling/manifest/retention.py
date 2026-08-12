#!/usr/bin/env python3
"""Retention policy for the build-lane haul packages (ADR 0033).

Every main build republishes the whole ~5GB platform to the two GHCR packages
`manifests/scout` (the .tar.zst bundle) and `manifests/scout-manifest` (the
manifest), tagged `0.YYYYMMDD.<run>` with `:main` moved onto the newest. Left
unbounded they accumulate ~5GB per build. This computes which package *versions*
to keep vs delete; the workflow feeds it `GET .../versions` and acts on the split.

Policy is by AGE, not count, so the rollback window is a fixed time horizon
regardless of build cadence (deliberately conservative -- deleting the wrong
version breaks Flux consumers that pin by digest, so we under-prune):
  - KEEP any version tagged `main` (the pointer the next build carries from).
  - KEEP version-tagged artifacts younger than `keep_days`.
  - KEEP at least the `min_keep` newest artifacts regardless of age, so a quiet
    period never prunes the whole rollback set.
  - KEEP a cosign signature (`sha256-<hex>.sig`) whose subject digest is a kept
    artifact; DELETE it only when its subject artifact is itself being deleted.
  - KEEP every untagged version (referrer-style signatures, or an in-flight push).
  - DELETE the remainder.

This module is import-only pure logic (unit-tested offline); the GHCR calls and
the two hard-coded package names live in the workflow so a bug here can never
reach a package it wasn't handed.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_SIG_TAG = re.compile(r"^sha256-([0-9a-f]{64})\.sig$")


def _sig_subject(tags):
    """Return the subject digest (sha256:<hex>) a cosign `.sig` tag refers to, else None."""
    for t in tags:
        m = _SIG_TAG.match(t)
        if m:
            return "sha256:" + m.group(1)
    return None


def _parse(created_at):
    """Parse a GHCR ISO timestamp; missing/empty sorts as oldest."""
    if not created_at:
        return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(created_at.replace("Z", "+00:00"))


def partition(versions, keep_days, min_keep, now):
    """Split GHCR package versions into (keep_ids, delete_ids).

    keep_days: retain version-tagged artifacts younger than this. min_keep: always
    retain at least this many newest artifacts regardless of age. now: the reference
    time (passed for testability). versions: dicts with id, created_at (ISO), tags,
    digest (the version's own sha256:...).
    """
    if min_keep < 1:
        raise ValueError("min_keep must be >= 1 (always keep at least the newest haul)")
    if keep_days < 0:
        raise ValueError("keep_days must be >= 0")
    cutoff = now - timedelta(days=keep_days)

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

    artifacts.sort(key=lambda v: (_parse(v.get("created_at")), v["id"]), reverse=True)
    kept_digests = set()
    for i, v in enumerate(artifacts):
        young = _parse(v.get("created_at")) >= cutoff
        if v["id"] in main_ids or i < min_keep or young:
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
    delete -= keep  # keep always wins if a version lands in both sets
    assert keep.isdisjoint(delete)
    return keep, delete


def versions_from_api(data):
    """Map a GHCR `GET .../versions` JSON list to partition() input dicts. GHCR
    container versions carry the full sha256:<hex> digest in `name` and the tags
    under metadata.container.tags."""
    return [
        {
            "id": v["id"],
            "created_at": v.get("created_at", ""),
            "digest": v.get("name", ""),
            "tags": ((v.get("metadata") or {}).get("container") or {}).get("tags")
            or [],
        }
        for v in data
    ]


def _main():
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="Compute haul package retention plan.")
    ap.add_argument(
        "--keep-days", type=int, default=30, help="retain hauls younger than this"
    )
    ap.add_argument(
        "--min-keep", type=int, default=5, help="always keep at least this many newest"
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
    versions = versions_from_api(json.loads(raw))
    keep, delete = partition(
        versions, args.keep_days, args.min_keep, datetime.now(timezone.utc)
    )

    by_id = {v["id"]: v for v in versions}
    print(
        f"total={len(versions)} keep={len(keep)} delete={len(delete)} "
        f"(keep_days={args.keep_days} min_keep={args.min_keep})",
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
