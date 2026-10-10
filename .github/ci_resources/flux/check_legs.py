#!/usr/bin/env python3
"""Keep the ingest and authentication legs aligned with the on-prem Flux DAG.

Each explicit wait list must equal its entrypoints' dependency closure. Root
patches suspend exactly the components outside it. Validate the whole DAG for
duplicate names, missing dependencies, and cycles, including suspended nodes.

Usage: check_legs.py [ingest-kustomizations.txt]
"""

import argparse
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
ROOTS = HERE / "roots-apps.yaml"
INGEST = HERE / "ingest-kustomizations.txt"
SETS = {
    "./flux": REPO / "deploy" / "flux",
    "./modes/on-prem": REPO / "deploy" / "modes" / "on-prem",
}
LEGS = {
    "ingest": {
        "entrypoints": ("extractor",),
        "wait": INGEST,
        "roots": ROOTS,
    },
    "auth": {
        "entrypoints": (
            "launchpad",
            "edge-on-prem",
            "trino-ro",
            "keycloak-fragment-reconciler",
        ),
        "wait": HERE / "auth-kustomizations.txt",
        "roots": HERE / "roots-apps-auth.yaml",
    },
}


class LegError(ValueError):
    """The CI leg no longer selects exactly its deployment dependencies."""


def kustomizations(directory: Path) -> dict:
    out = {}
    for f in sorted(directory.glob("*.yaml")):
        if f.name == "kustomization.yaml":
            continue
        for d in yaml.safe_load_all(f.read_text()):
            if d and d["kind"] == "Kustomization":
                if d.get("apiVersion") != "kustomize.toolkit.fluxcd.io/v1":
                    raise LegError("unexpected Kustomization API in " + str(f))
                if d["metadata"]["name"] in out:
                    raise LegError("duplicate Kustomization: " + d["metadata"]["name"])
                out[d["metadata"]["name"]] = [
                    x["name"] for x in d["spec"].get("dependsOn", [])
                ]
    return out


def dependency_closure(deps, entrypoints):
    complete, visiting = set(), set()

    def visit(name):
        if name not in deps:
            raise LegError("missing dependency or entrypoint: " + name)
        if name in visiting:
            raise LegError("dependency cycle reaches " + name)
        if name in complete:
            return
        visiting.add(name)
        for dependency in deps[name]:
            visit(dependency)
        visiting.remove(name)
        complete.add(name)

    for name in entrypoints:
        visit(name)
    return complete


def dependency_graph(by_set):
    deps = {}
    for components in by_set.values():
        duplicates = set(deps) & set(components)
        if duplicates:
            raise LegError(
                "duplicate Kustomizations across mode/shared sets: "
                + ", ".join(sorted(duplicates))
            )
        deps.update(components)
    dependency_closure(deps, deps)  # Validate components outside active legs too.
    return deps


def suspension_names(root, components):
    """Validate our explicit name-list selectors, not arbitrary patch programs."""
    suspended = set()
    for patch in root["spec"].get("patches", []):
        operations = yaml.safe_load(patch["patch"])
        if not isinstance(operations, list):
            raise LegError("leg root patches must use explicit JSON patch operations")
        for operation in operations:
            if operation.get("path") != "/spec/suspend":
                continue
            if (
                operation.get("op") not in ("add", "replace")
                or operation.get("value") is not True
            ):
                raise LegError("suspension patch must explicitly set true")
            target = patch.get("target", {})
            if target.get("kind") != "Kustomization" or set(target) - {"kind", "name"}:
                raise LegError("suspension must target Kustomizations by name only")
            selector = target.get("name", "")
            match = re.fullmatch(r"\^\(([a-z0-9-]+(?:\|[a-z0-9-]+)*)\)\$", selector)
            names = match.group(1).split("|") if match else [selector]
            if any(not re.fullmatch(r"[a-z0-9-]+", name) for name in names):
                raise LegError("suspension selector must list exact component names")
            if len(names) != len(set(names)) or suspended & set(names):
                raise LegError("duplicate suspension target")
            unknown = set(names) - set(components)
            if unknown:
                raise LegError(
                    "stale or wrong-root suspension targets: "
                    + ", ".join(sorted(unknown))
                )
            suspended.update(names)
    return suspended


def validate_leg(name, leg, by_set, deps):
    closure = dependency_closure(deps, leg["entrypoints"])
    wait_list = Path(leg["wait"]).read_text().split()
    if len(wait_list) != len(set(wait_list)):
        raise LegError(name + " wait list contains duplicate names")
    want = set(wait_list)
    if closure != want:
        raise LegError(
            "{} wait list differs from dependency closure: missing {}, extra {}".format(
                name, sorted(closure - want), sorted(want - closure)
            )
        )
    roots = list(yaml.safe_load_all(Path(leg["roots"]).read_text()))
    seen_paths, seen_names = set(), set()
    for root in roots:
        spec = root["spec"]
        path, root_name = spec["path"], root["metadata"]["name"]
        if path not in by_set or path in seen_paths or root_name in seen_names:
            raise LegError(name + " roots contain an unknown/duplicate path or name")
        seen_paths.add(path)
        seen_names.add(root_name)
        if (
            root.get("apiVersion") != "kustomize.toolkit.fluxcd.io/v1"
            or root.get("kind") != "Kustomization"
            or root["metadata"].get("namespace") != "flux-system"
            or spec.get("sourceRef")
            != {"kind": "OCIRepository", "name": "scout-config"}
            or spec.get("dependsOn") != [{"name": "scout-site-source"}]
            or spec.get("wait") is not False
            or spec.get("suspend", False)
        ):
            raise LegError(name + " root changed source, namespace, or site ordering")
        suspended = suspension_names(root, by_set[path])
        outside = set(by_set[path]) - closure
        if suspended != outside:
            raise LegError(
                "{} {} suspends {}, expected {}".format(
                    name, root_name, sorted(suspended), sorted(outside)
                )
            )
    if seen_paths != set(by_set):
        raise LegError(name + " roots do not cover both shared and on-prem sets")
    return closure


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ingest_list", nargs="?", type=Path, default=INGEST)
    args = parser.parse_args(argv)
    try:
        by_set = {path: kustomizations(directory) for path, directory in SETS.items()}
        deps = dependency_graph(by_set)
        for name, settings in LEGS.items():
            leg = dict(settings)
            if name == "ingest":
                leg["wait"] = args.ingest_list
            closure = validate_leg(name, leg, by_set, deps)
            print(
                "{} leg: {} Kustomizations; {} suspended".format(
                    name, len(closure), len(deps) - len(closure)
                )
            )
    except (LegError, OSError, yaml.YAMLError, KeyError, TypeError) as exc:
        print("::error::" + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
