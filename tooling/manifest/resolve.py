"""Resolve the build-lane digest set for the Hauler manifest (ADR 0033).

For a main build, each of the platform's components ends up pinned as
`repo:tag@sha256:...`. A component rebuilt this run uses its fresh push
tag+digest; an unchanged one carries its current tag+digest.

This module does only the assembly and fail-closed validation. *How* a carried
component's tag+digest is obtained, read from the predecessor haul, or
live-inspect the component's current stable tag, is an injected callable and
does not change the assembly. Feed the result to `haul.render_images`.

Dependency-free (stdlib only) so it runs on a CI runner with no install step.
"""

from __future__ import annotations

from typing import Callable, Optional

# A component's identity for the manifest: its published (tag, digest).
TagDigest = tuple[str, str]


def _pin(repo: str, td: Optional[TagDigest]) -> str:
    if td is None:
        raise ValueError(f"no tag+digest for {repo} (neither rebuilt nor carried)")
    tag, digest = td
    if not tag:
        raise ValueError(f"empty tag for {repo}")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ValueError(f"invalid digest for {repo}:{tag} (got {digest!r})")
    return f"{repo}:{tag}@{digest}"


def resolve_refs(
    components: list[str],
    *,
    fresh: dict[str, TagDigest],
    carry: Callable[[str], Optional[TagDigest]],
) -> list[str]:
    """Return `repo:tag@digest` for every component, in ``components`` order.

    ``fresh`` maps a repo rebuilt this run to its (tag, digest). For any other
    component ``carry(repo)`` supplies the carried (tag, digest); it is called
    only for components not in ``fresh``. Fail closed: a missing component
    (``carry`` returns None), an empty tag, or a non-``sha256:`` digest raises,
    so the manifest can never bundle an unpinned or absent component.
    """
    if not components:
        raise ValueError("resolve_refs requires at least one component")
    if len(set(components)) != len(components):
        dupes = sorted({c for c in components if components.count(c) > 1})
        raise ValueError(f"duplicate components: {dupes}")

    refs: list[str] = []
    for repo in components:
        td = fresh[repo] if repo in fresh else carry(repo)
        refs.append(_pin(repo, td))
    return refs
