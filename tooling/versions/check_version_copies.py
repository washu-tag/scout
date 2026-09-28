#!/usr/bin/env python3
"""Fail when copies of a pinned version disagree (issue #748).

``ansible/group_vars/all/versions.yaml`` is the source of truth for pinned versions, and
several are copied into other files: ``deploy/`` literals, ``keycloak/VERSION``, Dockerfile
``FROM`` lines. Renovate bumps every copy together, because it groups its customManagers'
matches by depName. This check catches a copy edited by hand, as ``keycloak/Dockerfile``
was in #706.

Versions are extracted with ``renovate.json5``'s own customManagers patterns, so any file
Renovate tracks is checked.

Usage: check_version_copies.py [REPO_ROOT]   (default: this repository)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def strip_json5(text: str) -> str:
    """JSON5 as renovate.json5 uses it (comments, trailing commas) -> JSON."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':  # copy a string verbatim, escapes included
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
        elif text.startswith("/*", i):
            i = text.index("*/", i) + 2
        else:
            out.append(c)
            i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _python_regex(pattern: str) -> re.Pattern:
    # JavaScript named groups (?<name>...) -> Python (?P<name>...); lookbehinds untouched.
    return re.compile(re.sub(r"\(\?<(?![=!])", "(?P<", pattern))


def collect(repo: Path = REPO) -> dict[str, list[tuple[str, str]]]:
    """depName -> [(file, version)] for every copy Renovate tracks."""
    config = json.loads(strip_json5((repo / "renovate.json5").read_text()))
    copies: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for manager in config.get("customManagers", []):
        patterns = [_python_regex(p) for p in manager["matchStrings"]]
        for glob in manager["managerFilePatterns"]:
            if glob.startswith("/"):
                raise ValueError(
                    f"regex managerFilePatterns aren't supported here: {glob}"
                )
            for path in sorted(repo.glob(glob)):
                text = path.read_text()
                for pattern in patterns:
                    for m in pattern.finditer(text):
                        groups = m.groupdict()
                        dep = groups.get("depName") or manager.get("depNameTemplate")
                        if dep and groups.get("currentValue"):
                            copies[dep].append(
                                (str(path.relative_to(repo)), groups["currentValue"])
                            )
    return copies


def find_drift(
    copies: dict[str, list[tuple[str, str]]]
) -> dict[str, list[tuple[str, str]]]:
    return {dep: rows for dep, rows in copies.items() if len({v for _, v in rows}) > 1}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("repo", nargs="?", type=Path, default=REPO)
    args = ap.parse_args(argv)
    copies = collect(args.repo)
    drift = find_drift(copies)
    for dep, rows in sorted(drift.items()):
        print(f"{dep} has different versions:")
        for path, version in rows:
            print(f"  {path}: {version}")
    if drift:
        sys.exit(
            "Make every copy match versions.yaml (see docs/internal/ci-security-scanning.md)."
        )
    shared = sum(1 for rows in copies.values() if len(rows) > 1)
    print(f"{len(copies)} pinned dependencies, {shared} with copies, all consistent")


if __name__ == "__main__":
    main()
