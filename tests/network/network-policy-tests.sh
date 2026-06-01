#!/usr/bin/env bash
# network-policy-tests.sh — Verify Scout NetworkPolicies are enforced
#
# Covers two policies:
#   - trino-rw   (scout-extractor): write-path Trino, restricted to the
#                hl7-transformer + Voila pods on port 8080.
#   - opa-trino  (scout-analytics): the AuthZ decision engine, restricted to
#                the trino-analytics coordinator on port 8181.
#
# Spins up short-lived pods in different namespaces / with different labels
# and confirms each can or can't reach its target. Runs all test pods in
# parallel and aggregates results, so total runtime is dominated by the
# slowest single test (~20s) rather than the sum of all retries.
#
# Each test pod uses a retry loop because kube-router takes a few seconds
# to incorporate new pod IPs into its allow-ipset, so we want the test to
# express "succeeds when it eventually succeeds" rather than rely on a
# fixed sleep.
#
# Expected matrix:
#   trino-rw (port 8080):
#     - pod in scout-analytics (any labels)                  → denied (000)
#     - pod in scout-extractor without transformer labels    → denied (000)
#     - pod in scout-extractor with hl7-transformer label     → allowed (200)
#   opa-trino (port 8181):
#     - pod in scout-analytics with coordinator labels        → allowed (200)
#     - pod in scout-analytics without coordinator labels     → denied (000)
#     - pod in scout-extractor (any labels)                  → denied (000)
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

# Targets. The trino-rw target is the default for queue_test; OPA tests
# override host/port/path per-test (queue_test args 5-7).
TRINO_RW_HOST=trino-rw.scout-extractor
TRINO_RW_PORT=8080
TRINO_RW_PATH=/v1/info
OPA_HOST=opa-trino.scout-analytics
OPA_PORT=8181
OPA_PATH=/health

# Retry curl up to 20 times so kube-router has time to update its ipset
# for new pods. Allowed pods exit fast; denied pods exhaust retries.
# curl writes "000" via -w on connection failure and exits non-zero, so
# we suppress the exit with || true and let curl produce the code.
# __HOST__/__PORT__/__PATH__ are substituted per-test at launch time.
RETRY_LOOP_TMPL='for i in $(seq 1 20); do
  code=$(curl -sS -o /dev/null --max-time 2 -w "%{http_code}" \
    "http://__HOST__:__PORT____PATH__" 2>/dev/null) || true
  if [ "$code" = "200" ]; then
    echo "RESULT=200"
    exit 0
  fi
  sleep 1
done
echo "RESULT=$code"
exit 1'

# ── Test queue ────────────────────────────────────────────────────────────────
# Parallel arrays. queue_test args: name namespace expected [labels] [host] [port] [path]
# host/port/path default to the trino-rw target when omitted.
NAMES=()
NAMESPACES=()
EXPECTED=()
LABELS=()
HOSTS=()
PORTS=()
PATHS=()

queue_test() {
  NAMES+=("$1")
  NAMESPACES+=("$2")
  EXPECTED+=("$3")
  LABELS+=("${4:-}")
  HOSTS+=("${5:-$TRINO_RW_HOST}")
  PORTS+=("${6:-$TRINO_RW_PORT}")
  PATHS+=("${7:-$TRINO_RW_PATH}")
}

# trino-rw: only hl7-transformer (and Voila, not exercised here) may reach it
queue_test nb-test               scout-analytics 000
queue_test chat-test              scout-analytics 000 'app.kubernetes.io/name=open-webui'
queue_test extractor-rando        scout-extractor 000
queue_test transformer-impostor   scout-extractor 200 'app.kubernetes.io/name=hl7-transformer'

# opa-trino: only the trino-analytics coordinator may reach the decision API
queue_test opa-coordinator-ok    scout-analytics 200 'app.kubernetes.io/name=trino,app.kubernetes.io/component=coordinator' "$OPA_HOST" "$OPA_PORT" "$OPA_PATH"
queue_test opa-analytics-rando   scout-analytics 000 '' "$OPA_HOST" "$OPA_PORT" "$OPA_PATH"
queue_test opa-extractor-rando   scout-extractor 000 '' "$OPA_HOST" "$OPA_PORT" "$OPA_PATH"

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

  echo "  launching $name (namespace=$namespace labels=${labels:-<none>} target=${HOSTS[$i]}:${PORTS[$i]}${PATHS[$i]})"

  label_arg=()
  [ -n "$labels" ] && label_arg=(--labels="$labels")

  loop=${RETRY_LOOP_TMPL//__HOST__/${HOSTS[$i]}}
  loop=${loop//__PORT__/${PORTS[$i]}}
  loop=${loop//__PATH__/${PATHS[$i]}}

  (
    $KUBECTL run "$name" -n "$namespace" --restart=Never --rm --attach=true \
      "${label_arg[@]}" --image=curlimages/curl --command -- \
      sh -c "$loop" >"$RESULTS_DIR/$name.out" 2>&1 || true
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
