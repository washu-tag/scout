#!/usr/bin/env python3
"""Keep the ingest leg in step with the artifact's DAG.

The leg waits on an explicit Kustomization list and suspends the rest through the
roots-apps.yaml patches. Both are hand-written, so assert them against the DAG in deploy/
(the shared ./flux set plus ./modes/on-prem): the list must equal the extractor's dependsOn
closure, and each root must suspend exactly its own Kustomizations outside it.

Usage: check_legs.py [ingest-kustomizations.txt]
"""

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
ROOTS = Path(__file__).resolve().parent / "roots-apps.yaml"
INGEST = Path(__file__).resolve().parent / "ingest-kustomizations.txt"
SETS = {
    "./flux": REPO / "deploy" / "flux",
    "./modes/on-prem": REPO / "deploy" / "modes" / "on-prem",
}


def kustomizations(directory: Path) -> dict:
    out = {}
    for f in sorted(directory.glob("*.yaml")):
        if f.name == "kustomization.yaml":
            continue
        for d in yaml.safe_load_all(f.read_text()):
            if d and d["kind"] == "Kustomization":
                out[d["metadata"]["name"]] = [
                    x["name"] for x in d["spec"].get("dependsOn", [])
                ]
    return out


def main() -> None:
    ingest = Path(sys.argv[1]) if len(sys.argv) > 1 else INGEST
    want = set(ingest.read_text().split())
    by_set = {path: kustomizations(d) for path, d in SETS.items()}
    deps = {n: ds for ks in by_set.values() for n, ds in ks.items()}
    closure, todo = set(), ["extractor"]
    while todo:
        n = todo.pop()
        if n not in closure:
            closure.add(n)
            todo.extend(deps[n])
    problems = []
    if closure != want:
        problems.append(
            "ingest list differs from the extractor closure: missing {}, extra {}".format(
                sorted(closure - want), sorted(want - closure)
            )
        )
    for root in yaml.safe_load_all(ROOTS.read_text()):
        path = root["spec"]["path"]
        suspended = set()
        for p in root["spec"].get("patches", []):
            name = p["target"].get("name")
            if name and "/spec/suspend" in p["patch"]:
                rx = re.compile(r"^(?:{})$".format(name))
                suspended |= {n for n in by_set[path] if rx.match(n)}
        outside = set(by_set[path]) - closure
        if suspended != outside:
            problems.append(
                "{} ({}) suspends {}, but its Kustomizations outside the closure are {}".format(
                    root["metadata"]["name"], path, sorted(suspended), sorted(outside)
                )
            )
    for p in problems:
        print("::error::" + p)
    if problems:
        sys.exit(1)
    print(
        "ingest leg: {} Kustomizations (the extractor closure); {} suspended".format(
            len(closure), len(deps) - len(closure)
        )
    )


if __name__ == "__main__":
    main()
