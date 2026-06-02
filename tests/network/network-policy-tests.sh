#!/usr/bin/env bash
# network-policy-tests.sh — Verify Scout NetworkPolicies are enforced
#
# Spins up short-lived pods in different namespaces / with different labels
# and confirms each can or can't reach a given target on its declared port.
# Runs all test pods in parallel and aggregates results, so total runtime is
# dominated by the slowest single test (~20s) rather than the sum of all
# retries.
#
# Each test pod uses a retry loop because kube-router takes a few seconds
# to incorporate new pod IPs into its allow-ipset, so we want the test to
# express "succeeds when it eventually succeeds" rather than rely on a
# fixed sleep.
#
# Coverage today:
#   trino-rw (port 8080) — ingress restricted to hl7-transformer pods in
#     scout-extractor + voila pods in scout-analytics. Verifies both the
#     allow paths and the deny path (random pods in either namespace).
#   voila (port 8866) — ingress restricted to traefik in kube-system.
#     Verifies a random scout-analytics pod can't connect to Voila
#     directly (which would forge X-Trino-User otherwise).
#
# Exit 0 if all tests pass, non-zero on any failure.

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Config ────────────────────────────────────────────────────────────────────
KUBECTL=${KUBECTL:-kubectl}

# Test groups can be filtered via --include for split CI runs where not
# every target service is deployed. Empty = run all groups.
#   trino-rw        — works wherever the trino role has been run (deploys
#                     trino-rw + its NetworkPolicy). Both smoke-test sides.
#   voila-ingress   — needs the voila Service to exist for the target URL
#                     to resolve. Notebook side only.
INCLUDE_GROUPS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --include)
      INCLUDE_GROUPS="$2"
      shift 2
      ;;
    --help)
      cat <<EOF
Usage: $(basename "$0") [--include <groups>]

  --include <groups>   Comma-separated test groups to run (default: all).
                       Available: trino-rw, voila-ingress.

Examples:
  $(basename "$0")
  $(basename "$0") --include trino-rw
  $(basename "$0") --include trino-rw,voila-ingress
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

include_group() {
  local group="$1"
  [[ -z "$INCLUDE_GROUPS" ]] && return 0
  for inc in $(echo "$INCLUDE_GROUPS" | tr ',' ' '); do
    [[ "$group" == "$inc" ]] && return 0
  done
  return 1
}

# Per-target URLs. /v1/info is unauthenticated on Trino's read + write paths;
# Voila's root (`/`) returns its lab page without auth at the in-cluster
# Service level — oauth2-proxy is only in the path on ingress-routed traffic,
# not direct pod-to-Service calls.
TRINO_RW_URL='http://trino-rw.scout-extractor:8080/v1/info'
VOILA_URL='http://voila.scout-analytics:8866/'

# Retry curl up to 20 times so kube-router has time to update its ipset
# for new pods. Allowed pods exit fast; denied pods exhaust retries.
# curl writes "000" via -w on connection failure and exits non-zero, so
# we suppress the exit with || true and let curl produce the code.
build_retry_loop() {
  local url="$1"
  cat <<EOF
for i in \$(seq 1 20); do
  code=\$(curl -sS -o /dev/null --max-time 2 -w "%{http_code}" \\
    "$url" 2>/dev/null) || true
  if [ "\$code" = "200" ]; then
    echo "RESULT=200"
    exit 0
  fi
  sleep 1
done
echo "RESULT=\$code"
exit 1
EOF
}

# ── Test queue ────────────────────────────────────────────────────────────────
# Parallel arrays: NAMES[i], NAMESPACES[i], EXPECTED[i], LABELS[i], URLS[i]
NAMES=()
NAMESPACES=()
EXPECTED=()
LABELS=()
URLS=()

queue_test() {
  NAMES+=("$1")
  NAMESPACES+=("$2")
  EXPECTED+=("$3")
  LABELS+=("${4:-}")
  URLS+=("${5:-$TRINO_RW_URL}")
}

# trino-rw matrix (port 8080).
if include_group "trino-rw"; then
  queue_test nb-test               scout-analytics 000
  queue_test chat-test              scout-analytics 000 'app.kubernetes.io/name=open-webui'
  queue_test extractor-rando        scout-extractor 000
  queue_test transformer-impostor   scout-extractor 200 'app.kubernetes.io/name=hl7-transformer'
  # Positive Voila → trino-rw check: trino-rw's NetworkPolicy explicitly
  # allow-lists voila-labeled pods in scout-analytics for the reviewer-
  # annotation write path. Catches regressions where the allow rule gets
  # dropped without updating Voila's connect_rw() callers. Test is
  # synthetic (curl pod with voila label) so it doesn't require the
  # voila Deployment to be present.
  queue_test voila-rw-impostor      scout-analytics 200 'app.kubernetes.io/name=voila'
fi

# voila ingress matrix (port 8866).
# Voila's X-Trino-User impersonation trust model depends on the ingress
# NetworkPolicy: only Traefik should reach Voila directly. A random
# in-cluster pod hitting :8866 could forge X-Auth-Request-Access-Token
# headers and bypass per-user AuthZ.
if include_group "voila-ingress"; then
  queue_test voila-bypass-attack    scout-analytics 000 ''                                        "$VOILA_URL"
  queue_test voila-traefik-allowed  kube-system     200 'app.kubernetes.io/name=traefik'          "$VOILA_URL"
fi

if [ "${#NAMES[@]}" -eq 0 ]; then
  echo "No tests selected (--include=$INCLUDE_GROUPS); nothing to run." >&2
  exit 2
fi

# ── Workspace + cleanup ───────────────────────────────────────────────────────
RESULTS_DIR=$(mktemp -d)
cleanup() {
  # Kill any still-running kubectl processes
  jobs -p | xargs -r kill 2>/dev/null || true
  # Best-effort delete any pods that --rm didn't catch (e.g. on signal)
  for i in "${!NAMES[@]}"; do
    $KUBECTL delete pod -n "${NAMESPACES[$i]}" "${NAMES[$i]}" \
      --ignore-not-found --wait=false >/dev/null 2>&1 || true
  done
  rm -rf "$RESULTS_DIR"
}
trap cleanup EXIT

# ── Launch all in parallel ────────────────────────────────────────────────────
echo -e "${BOLD}Running NetworkPolicy tests in parallel...${RESET}"
echo

for i in "${!NAMES[@]}"; do
  name=${NAMES[$i]}
  namespace=${NAMESPACES[$i]}
  labels=${LABELS[$i]}
  url=${URLS[$i]}

  echo "  launching $name (namespace=$namespace labels=${labels:-<none>} target=$url)"

  label_arg=()
  [ -n "$labels" ] && label_arg=(--labels="$labels")

  retry_script=$(build_retry_loop "$url")

  (
    $KUBECTL run "$name" -n "$namespace" --restart=Never --rm --attach=true \
      "${label_arg[@]}" --image=curlimages/curl --command -- \
      sh -c "$retry_script" >"$RESULTS_DIR/$name.out" 2>&1 || true
  ) &
done

echo
echo "  waiting for all tests to complete..."
wait
echo

# ── Aggregate results ─────────────────────────────────────────────────────────
PASS=0
FAIL=0
FAILED_NAMES=()

for i in "${!NAMES[@]}"; do
  name=${NAMES[$i]}
  expected=${EXPECTED[$i]}
  out_file="$RESULTS_DIR/$name.out"

  actual=$(grep -oE 'RESULT=[0-9]+' "$out_file" 2>/dev/null | tail -1 | cut -d= -f2)
  actual=${actual:-no-result}

  if [ "$actual" = "$expected" ]; then
    echo -e "  ${GREEN}PASS${RESET} $name (got RESULT=$actual)"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}FAIL${RESET} $name (got RESULT=$actual, expected RESULT=$expected)"
    echo "  --- Pod output ---"
    sed 's/^/    /' "$out_file"
    echo "  ------------------"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo
echo -e "${BOLD}Results:${RESET} ${GREEN}$PASS passed${RESET}, ${RED}$FAIL failed${RESET}"

if [ "$FAIL" -gt 0 ]; then
  echo -e "${RED}Failed tests:${RESET} ${FAILED_NAMES[*]}"
  exit 1
fi
