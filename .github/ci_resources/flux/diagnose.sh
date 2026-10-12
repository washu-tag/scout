#!/usr/bin/env bash
# Failure diagnostics for the Flux proof. Lists Secret names only, never their data.
set -uo pipefail
group() { echo "::group::$1"; }
endgroup() { echo "::endgroup::"; }

group "Flux sources, Kustomizations, HelmReleases"
flux get sources all -A 2>&1
flux get kustomizations -A 2>&1
flux get helmreleases -A 2>&1
endgroup

group "Not-ready HelmReleases (status)"
kubectl get helmreleases -A -o json | python3 -c '
import json, sys
for h in json.load(sys.stdin)["items"]:
    conds = {c["type"]: c for c in h.get("status", {}).get("conditions", [])}
    if conds.get("Ready", {}).get("status") != "True":
        print("== {}/{}".format(h["metadata"]["namespace"], h["metadata"]["name"]))
        for c in conds.values():
            print("   {} {} {}: {}".format(c["type"], c["status"], c.get("reason"), c.get("message", "")[:600]))
'
endgroup

group "Pods"
kubectl get pods -A -o wide 2>&1
endgroup

group "Pods not running cleanly (describe + logs)"
kubectl get pods -A -o json | python3 -c '
import json, sys
for p in json.load(sys.stdin)["items"]:
    st = p.get("status", {})
    cs = st.get("containerStatuses", []) + st.get("initContainerStatuses", [])
    bad = st.get("phase") not in ("Running", "Succeeded") or any(
        c.get("restartCount", 0) > 0 or (st.get("phase") == "Running" and not c.get("ready") and c.get("state", {}).get("terminated") is None)
        for c in cs)
    if bad:
        print(p["metadata"]["namespace"], p["metadata"]["name"])
' | while read -r ns pod; do
  echo "=== ${ns}/${pod}"
  kubectl describe pod -n "$ns" "$pod" 2>&1 | sed -n '/^Containers:/,$p' | tail -60
  kubectl logs -n "$ns" "$pod" --all-containers --tail=80 --prefix 2>&1 | tail -120
  kubectl logs -n "$ns" "$pod" --all-containers --tail=40 --prefix --previous 2>/dev/null | tail -60
done
endgroup

group "Events (last 120)"
kubectl get events -A --sort-by=.lastTimestamp 2>&1 | tail -120
endgroup

group "Secrets (names only)"
kubectl get secrets -A --no-headers -o custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,TYPE:.type 2>&1 \
  | grep -v 'helm.sh/release.v1'
endgroup

group "Node capacity"
kubectl describe node | sed -n '/Allocated resources/,/Events:/p'
free -m
df -h / /mnt
endgroup

group "Flux controller logs"
for c in kustomize-controller helm-controller source-controller; do
  echo "=== ${c}"
  kubectl -n flux-system logs "deploy/${c}" --tail=120 2>&1
done
endgroup
