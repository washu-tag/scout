#!/usr/bin/env python3
"""Wait for an explicit list of Flux Kustomizations in flux-system to be Ready.

Not `kubectl wait --all`: the leg suspends the rest of the DAG, and a suspended
Kustomization never turns Ready. Prints a status table whenever it changes (at least every
5 minutes), and fails early when a HelmRelease stalls, since a stalled release only
recovers on a spec change.

Usage: wait_ready.py "<space-separated names>" <timeout seconds>
"""

import json
import subprocess
import sys
import time


def items(kind):
    p = subprocess.run(["kubectl", "get", kind, "-A", "-o", "json"], capture_output=True, text=True)
    return json.loads(p.stdout)["items"] if p.returncode == 0 else []


def condition(obj, kind):
    for c in obj.get("status", {}).get("conditions", []):
        if c["type"] == kind:
            return c
    return None


def main() -> None:
    names, timeout = sys.argv[1].split(), int(sys.argv[2])
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
            current = bool(o) and o.get("status", {}).get("observedGeneration") == o["metadata"]["generation"]
            ok = current and c is not None and c["status"] == "True"
            done += ok
            state = "Ready" if ok else (c["reason"] if c else ("missing" if not o else "Pending"))
            rows.append((n, state, "" if ok or not c else c.get("message", "")[:160]))
        elapsed = int(time.time() - start)
        if rows != shown or time.time() - shown_at > 300:
            print("--- {}/{} Ready after {}s".format(done, len(names), elapsed))
            for n, state, msg in rows:
                print("  {:28} {:22} {}".format(n, state, msg))
            sys.stdout.flush()
            shown, shown_at = rows, time.time()
        if done == len(names):
            print("all {} Kustomizations Ready in {}s".format(len(names), elapsed))
            return
        stalled = [
            "{}/{}: {}".format(h["metadata"]["namespace"], h["metadata"]["name"], condition(h, "Stalled").get("message", ""))
            for h in items("helmreleases.helm.toolkit.fluxcd.io")
            if (condition(h, "Stalled") or {}).get("status") == "True"
        ]
        if stalled:
            for s in stalled:
                print("::error::HelmRelease stalled: " + s[:400])
            sys.exit(1)
        if elapsed > timeout:
            print("::error::not Ready after {}s: {}".format(timeout, [r[0] for r in rows if r[1] != "Ready"]))
            sys.exit(1)
        time.sleep(15)


if __name__ == "__main__":
    main()
