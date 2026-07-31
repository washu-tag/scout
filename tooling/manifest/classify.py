"""Change classifier for the Scout build lane (ADR 0030).

Given what changed in a push (the CI ``changes`` job's per-component flags plus
the ``ci`` flag that fires on any workflow/action edit), decide the build's
rebuild SCOPE and which components are rebuilt vs carried:

- ``rebuild-all``     every component is rebuilt. A workflow/action change can
                      alter any image's build, and the weekly job forces this as
                      a misclassification backstop. Nothing is carried.
- ``rebuild-changed`` only components whose sources changed are rebuilt; the rest
                      carry their predecessor digest forward.
- ``skip``            nothing changed, so every component carries forward and no
                      image work runs.

``scope`` is the build decision; ``Classification.rebuild_scope`` derives the
schema-valid manifest ``rebuildScope`` from it. ``rebuild`` names the components
to build into ``manifest.carry_section``'s ``fresh_*`` artifacts, and ``carry``
is the complement it pulls from the predecessor.

Caller preconditions (the publish step, PR4): any carry, and therefore ``skip``,
presupposes a predecessor manifest covering every name, so force ``rebuild-all``
on the first build (no predecessor) or when ``names`` gains a component the
predecessor lacks; and build exactly the names in ``rebuild`` into ``fresh_*`` so
``carry_section`` cannot mistake a failed build for an intentional carry.

This module is dependency-free (stdlib only) so it runs on a CI runner with no
install step.
"""

from __future__ import annotations

from dataclasses import dataclass

SCOPE_ALL = "rebuild-all"
SCOPE_CHANGED = "rebuild-changed"
SCOPE_SKIP = "skip"


def _is_changed(name: str, value: object) -> bool:
    """Parse one change flag, failing closed on anything unrecognized.

    The CI ``changes`` job emits the strings 'true'/'false'; bools are accepted
    for direct callers. Anything else (``None``, ``''``, an int, an unknown
    string) is drift, not a legitimate "unchanged" signal. GitHub Actions
    resolves a missing output to ``''``, so coercing it to unchanged would carry
    a possibly-stale digest, exactly the drift the classifier must catch, so it
    raises instead.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v == "true":
            return True
        if v == "false":
            return False
    raise ValueError(f"unrecognized change flag for {name!r}: {value!r}")


@dataclass(frozen=True)
class Classification:
    """The rebuild decision for one build over one component set."""

    scope: str  # SCOPE_ALL | SCOPE_CHANGED | SCOPE_SKIP
    rebuild: list[str]  # built this run; become carry_section fresh_* artifacts
    carry: list[str]  # carried unchanged from the predecessor manifest

    @property
    def rebuild_scope(self) -> str:
        """The schema-valid manifest ``rebuildScope`` ('all' | 'changed').

        A skip build that still publishes carries everything, i.e. a 'changed'
        build with nothing fresh, so it maps to 'changed' too.
        """
        return "all" if self.scope == SCOPE_ALL else "changed"


def classify(
    names: list[str],
    changed: dict,
    *,
    ci: bool = False,
    force_all: bool = False,
) -> Classification:
    """Classify a build over ``names`` (images or charts), preserving their order.

    ``changed`` maps each component name to its ``changes``-job flag ('true' /
    'false' strings, or bools). ``ci`` (a workflow/action edit) and ``force_all``
    (the weekly backstop or a manual dispatch) each force ``rebuild-all``.

    Fail closed on drift: outside a full rebuild every name must carry a
    recognized flag; an absent key, or an unrecognized value (``''``, ``None``,
    garbage), would silently carry a stale digest, so it raises instead.
    """
    if not names:
        raise ValueError("classify requires a non-empty name list")
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"duplicate names: {dupes}")

    if ci or force_all:
        return Classification(SCOPE_ALL, list(names), [])

    missing = [n for n in names if n not in changed]
    if missing:
        raise ValueError(
            f"no change flag for {missing}; the name list drifted from the changes job"
        )

    rebuild = [n for n in names if _is_changed(n, changed[n])]
    if not rebuild:
        return Classification(SCOPE_SKIP, [], list(names))
    rebuilt = set(rebuild)
    carry = [n for n in names if n not in rebuilt]
    return Classification(SCOPE_CHANGED, rebuild, carry)
