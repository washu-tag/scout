"""Render a Hauler content manifest for the Scout build lane (ADR 0033).

Given the resolved digest set for a build, every image and OCI chart pinned as
`ref@sha256:...`, emit a Hauler `kind: Images` manifest that `hauler store sync`
bundles into the signed, air-gap-relocatable haul.

Everything (images and OCI charts alike) rides under `kind: Images` so
`hauler store copy` relocates each artifact verbatim, digest and repo path
intact. `kind: Charts` is deliberately not used: it re-packages a chart under a
`hauler/<name>` path with a new digest, which would break the digest and path
Flux pins (ADR 0033, POC-confirmed).

This module is dependency-free (stdlib only) so it runs on a CI runner with no
install step. It only renders; resolving each component's `ref@digest` (fresh
build digest for changed components, current stable-tag digest for carried ones)
is the caller's I/O step.
"""

from __future__ import annotations

APIVERSION = "content.hauler.cattle.io/v1"
_DIGEST_MARKER = "@sha256:"


def render_images(refs: list[str], *, name: str = "scout") -> str:
    """Render a Hauler `kind: Images` manifest pinning every ref by digest.

    ``refs`` is the whole platform's pinned references, e.g.
    ``ghcr.io/washu-tag/hl7-listener:0.20260803.1@sha256:...`` for images and
    ``ghcr.io/washu-tag/charts/hl7-listener:0.20260803.1@sha256:...`` for OCI
    charts. Order is preserved.

    Fail closed on drift: every ref must be digest-pinned (contain
    ``@sha256:``), and duplicates are rejected, so the manifest can never bundle
    an unpinned or repeated component.
    """
    if not refs:
        raise ValueError("render_images requires at least one ref")
    if len(set(refs)) != len(refs):
        dupes = sorted({r for r in refs if refs.count(r) > 1})
        raise ValueError(f"duplicate refs: {dupes}")
    unpinned = [r for r in refs if _DIGEST_MARKER not in r]
    if unpinned:
        raise ValueError(f"refs must be digest-pinned (contain @sha256:): {unpinned}")

    lines = [
        f"apiVersion: {APIVERSION}",
        "kind: Images",
        "metadata:",
        f"  name: {name}",
        "spec:",
        "  images:",
    ]
    lines += [f"    - name: {r}" for r in refs]
    return "\n".join(lines) + "\n"
