#!/usr/bin/env python3
"""Wait for an explicit list of Flux Kustomizations in flux-system to be Ready.

Not `kubectl wait --all`: the leg suspends the rest of the DAG, and a suspended
Kustomization never turns Ready. Prints a status table whenever it changes (at least every
5 minutes), and fails early when a HelmRelease stalls or a current-generation
Kustomization cannot compile its local manifests. Dependency, source-fetch,
missing-CRD and health-check failures can recover while the DAG starts up.

Usage: wait_ready.py "<space-separated names>" <timeout seconds>
"""

import json
import subprocess
import sys
import time


def items(kind):
    p = subprocess.run(
        ["kubectl", "get", kind, "-A", "-o", "json"], capture_output=True, text=True
    )
    return json.loads(p.stdout)["items"] if p.returncode == 0 else []


def condition(obj, kind):
    for c in obj.get("status", {}).get("conditions", []):
        if c["type"] == kind:
            return c
    return None


def local_build_failure(obj):
    """A known deterministic compile error in this spec, never an old verdict.

    Use the condition's generation: unsuccessful reconciliation need not advance
    status.observedGeneration. Do not reject every BuildFailed; a remote Kustomize
    base or post-build substitution may still be waiting on external inputs.
    """
    if not obj or obj.get("spec", {}).get("suspend", False):
        return None
    ready = condition(obj, "Ready") or {}
    generation = obj.get("metadata", {}).get("generation")
    if (
        type(generation) is not int
        or type(ready.get("observedGeneration")) is not int
        or ready["observedGeneration"] != generation
        or ready.get("status") != "False"
        or ready.get("reason") != "BuildFailed"
    ):
        return None
    message = ready.get("message", "")
    if not message.startswith("kustomize build failed:"):
        return None
    deterministic = (
        "no such file or directory",
        "MalformedYAMLError",
        "yaml: line ",
        "json: unknown field ",
        "json: cannot unmarshal ",
        "may not add resource with an already registered id",
        "no matches for Id ",
        "failed to find unique target for patch",
        "no resource matches strategic merge patch",
        "add operation does not apply: doc is missing path",
        "replace operation does not apply: doc is missing path",
    )
    if any(error in message for error in deterministic):
        return message
    return None


def wait_for_ready(names, timeout):
    start = time.time()
    shown, shown_at = None, 0.0
    while True:
        ks = {
            o["metadata"]["name"]: o
            for o in items("kustomizations.kustomize.toolkit.fluxcd.io")
            if o["metadata"]["namespace"] == "flux-system"
        }
        rows, done = [], 0
        for n in names:
            o, c = ks.get(n), condition(ks.get(n) or {}, "Ready")
            current = (
                bool(o)
                and o.get("status", {}).get("observedGeneration")
                == o["metadata"]["generation"]
            )
            ok = current and c is not None and c["status"] == "True"
            done += ok
            state = (
                "Ready"
                if ok
                else (c["reason"] if c else ("missing" if not o else "Pending"))
            )
            rows.append((n, state, "" if ok or not c else c.get("message", "")[:160]))
        elapsed = int(time.time() - start)
        if rows != shown or time.time() - shown_at > 300:
            print("--- {}/{} Ready after {}s".format(done, len(names), elapsed))
            for n, state, msg in rows:
                print("  {:28} {:22} {}".format(n, state, msg))
            sys.stdout.flush()
            shown, shown_at = rows, time.time()
        build_failures = [(name, local_build_failure(ks.get(name))) for name in names]
        fatal = [(name, message) for name, message in build_failures if message]
        if fatal:
            for name, message in fatal:
                print(
                    "::error::Kustomization flux-system/{} cannot build: {}".format(
                        name, message[:800]
                    )
                )
            sys.exit(1)
        if done == len(names):
            print("all {} Kustomizations Ready in {}s".format(len(names), elapsed))
            return
        stalled = [
            "{}/{}: {}".format(
                h["metadata"]["namespace"],
                h["metadata"]["name"],
                condition(h, "Stalled").get("message", ""),
            )
            for h in items("helmreleases.helm.toolkit.fluxcd.io")
            if (condition(h, "Stalled") or {}).get("status") == "True"
        ]
        if stalled:
            for s in stalled:
                print("::error::HelmRelease stalled: " + s[:400])
            sys.exit(1)
        if elapsed > timeout:
            print(
                "::error::not Ready after {}s: {}".format(
                    timeout, [r[0] for r in rows if r[1] != "Ready"]
                )
            )
            sys.exit(1)
        time.sleep(15)


def main() -> None:
    wait_for_ready(sys.argv[1].split(), int(sys.argv[2]))


if __name__ == "__main__":
    main()
