#!/usr/bin/env bash
# network-policy-tests.sh — Verify trino-rw NetworkPolicy is enforced
#
# Spins up short-lived pods in different namespaces / with different labels
# and confirms each can or can't reach trino-rw on port 8080. Runs all test
# pods in parallel and aggregates results, so total runtime is dominated by
# the slowest single test (~20s) rather than the sum of all retries.
#
# Each test pod uses a retry loop because kube-router takes a few seconds
# to incorporate new pod IPs into its allow-ipset, so we want the test to
# express "succeeds when it eventually succeeds" rather than rely on a
# fixed sleep.
#
# Expected matrix:
#   - pod in scout-analytics (any labels)                     → denied (000)
#   - pod in scout-extractor without transformer labels       → denied (000)
#   - pod in scout-extractor with hl7-transformer label       → allowed (200)
#
# Exit 0 if all tests pass, non-zero on any failure.

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Config ────────────────────────────────────────────────────────────────────
TRINO_RW_HOST=trino-rw.scout-extractor
TRINO_RW_PORT=8080
TRINO_RW_PATH=/v1/info
KUBECTL=${KUBECTL:-kubectl}

# Retry curl up to 20 times (1s between) so kube-router has time to update
# its ipset for new pods. Allowed pods exit fast; denied pods exhaust retries.
RETRY_LOOP="$(cat <<'EOF'
for i in $(seq 1 20); do
  code=$(curl -sS -o /dev/null --max-time 2 -w "%{http_code}" \
    "http://__HOST__:__PORT____PATH__" 2>/dev/null || echo 000)
  if [ "$code" = "200" ]; then
    echo "RESULT=200"
    exit 0
  fi
  sleep 1
done
echo "RESULT=$code"
exit 1
EOF
)"
RETRY_LOOP=${RETRY_LOOP//__HOST__/$TRINO_RW_HOST}
RETRY_LOOP=${RETRY_LOOP//__PORT__/$TRINO_RW_PORT}
RETRY_LOOP=${RETRY_LOOP//__PATH__/$TRINO_RW_PATH}

# ── Test queue ────────────────────────────────────────────────────────────────
# Parallel arrays: NAMES[i], NAMESPACES[i], EXPECTED[i], LABELS[i]
NAMES=()
NAMESPACES=()
EXPECTED=()
LABELS=()

queue_test() {
  NAMES+=("$1")
  NAMESPACES+=("$2")
  EXPECTED+=("$3")
  LABELS+=("${4:-}")
}

queue_test nb-test               scout-analytics 000
queue_test chat-test              scout-analytics 000 'app.kubernetes.io/name=open-webui'
queue_test extractor-rando        scout-extractor 000
queue_test transformer-impostor   scout-extractor 200 'app.kubernetes.io/name=hl7-transformer'

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
echo -e "${BOLD}Running trino-rw NetworkPolicy tests in parallel...${RESET}"
echo

for i in "${!NAMES[@]}"; do
  name=${NAMES[$i]}
  namespace=${NAMESPACES[$i]}
  labels=${LABELS[$i]}

  echo "  launching $name (namespace=$namespace labels=${labels:-<none>})"

  label_arg=()
  [ -n "$labels" ] && label_arg=(--labels="$labels")

  (
    $KUBECTL run "$name" -n "$namespace" --restart=Never --rm --attach=true \
      "${label_arg[@]}" --image=curlimages/curl --command -- \
      sh -c "$RETRY_LOOP" >"$RESULTS_DIR/$name.out" 2>&1 || true
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
