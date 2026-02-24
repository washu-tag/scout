#!/usr/bin/env bash
# auth-curl-tests.sh — Unauthenticated Access Verification
#
# Verifies that all protected Scout endpoints reject unauthenticated requests
# (no cookies, no tokens) and that unprotected endpoints remain accessible.
# Tests run over both HTTPS and HTTP for the full test matrix.
#
# Usage:
#   ./auth-curl-tests.sh scout.example.com
#   ./auth-curl-tests.sh scout.example.com --timeout 15

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Defaults ──────────────────────────────────────────────────────────────────
HOSTNAME=""
TIMEOUT=10

# ── Usage ─────────────────────────────────────────────────────────────────────
usage() {
  cat <<EOF
Usage: $(basename "$0") <hostname> [OPTIONS]

Scout Auth Curl Tests: Verify unauthenticated users cannot access protected Scout endpoints.

Arguments:
  hostname            Scout base hostname (e.g., scout.example.com)

Options:
  --timeout <secs>    curl timeout in seconds (default: 10)
  --help              Show this help message

Examples:
  $(basename "$0") scout.example.com
  $(basename "$0") scout.example.com --timeout 15
EOF
  exit 1
}

# ── Parse arguments ───────────────────────────────────────────────────────────
if [[ $# -eq 0 || "$1" == "--help" ]]; then
  usage
fi

HOSTNAME="$1"
shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    --timeout)
      TIMEOUT="$2"
      shift 2
      ;;
    --help)
      usage
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      ;;
  esac
done

# ── Test definitions ─────────────────────────────────────────────────────────
# Each test is 5 consecutive array elements:
#   subdomain  method  path  expected_status  "description"
#
# - subdomain: service subdomain (empty string "" for root/launchpad)
# - method: HTTP method (GET, POST, PUT, DELETE)
# - path: URL path to test
# - expected_status: expected HTTP status code
# - description: human-readable test name (quoted)
TESTS=(
  # Launchpad (empty subdomain = root hostname)
  "" GET / 401 "Launchpad root"
  "" GET /api/auth/callback/keycloak 401 "Launchpad NextAuth callback"

  # Superset
  superset GET / 401 "Superset root"
  superset GET /oauth-authorized/keycloak 401 "Superset OAuth callback"
  superset GET /login/ 401 "Superset login"
  superset GET /logout/ 401 "Superset logout"
  superset GET /dashboard/list/ 401 "Superset dashboard listing"
  superset GET /api/v1/dashboard/ 401 "Superset dashboard API"
  superset GET /sqllab/ 401 "Superset SQL Lab"
  superset GET /superset/welcome/ 401 "Superset welcome page"

  # JupyterHub
  jupyter GET / 401 "JupyterHub root"
  jupyter GET /hub/ 401 "JupyterHub hub page"
  jupyter GET /hub/home 401 "JupyterHub home page"
  jupyter GET /hub/oauth_callback 401 "JupyterHub OAuth callback"
  jupyter GET /hub/spawn 401 "JupyterHub spawn page"
  jupyter POST /hub/spawn 401 "JupyterHub spawn action"
  testuser.jupyter GET /user/testuser/ 401 "JupyterHub user notebook"

  # Open WebUI (Chat)
  chat GET / 401 "Open WebUI root"
  chat GET /oauth/oidc/callback 401 "Open WebUI OIDC callback"
  chat GET /api/v1/chats/ 401 "Open WebUI chats API"
  chat POST /api/v1/chats/00000000-0000-0000-0000-000000000000 401 "Open WebUI API create chat"
  chat DELETE /api/v1/chats/00000000-0000-0000-0000-000000000000 401 "Open WebUI API delete specific chat"

  # Grafana
  grafana GET / 401 "Grafana root"
  grafana GET /login/generic_oauth 401 "Grafana OAuth callback"
  grafana GET /dashboards 401 "Grafana dashboards listing"
  grafana GET /alerting/list 401 "Grafana alert listing"
  grafana GET /admin 401 "Grafana admin page"

  # Temporal
  temporal GET / 401 "Temporal UI root"
  temporal GET /auth/sso/callback 401 "Temporal SSO callback"
  temporal GET /login 401 "Temporal UI login page"
  temporal GET /namespaces/default/workflows 401 "Temporal UI workflows API"

  # MinIO Console
  minio GET / 401 "MinIO root"
  minio GET /oauth_callback 401 "MinIO OAuth callback"
  minio GET /browser/ 401 "MinIO browser UI"
  minio GET /browser/lake/hl7 401 "MinIO browser HL7 bucket"

  # Voila (Playbooks)
  playbooks GET / 401 "Playbooks root"
  playbooks GET /voila/render/cohort/Cohort.ipynb 401 "Playbooks Voila render"

  # Keycloak (unprotected — must be accessible for OAuth flows)
  keycloak GET /realms/scout/.well-known/openid-configuration 200 "Keycloak OIDC discovery"
  keycloak GET /realms/scout 200 "Keycloak realm info"
  keycloak GET /realms/master 200 "Keycloak master realm info"
  keycloak GET /realms/master/.well-known/openid-configuration 200 "Keycloak master OIDC discovery"
  keycloak GET /admin/master/console/ 200 "Keycloak admin console"

  # OAuth2 Proxy (sign-in page must be accessible)
  auth GET /oauth2/sign_in 200 "OAuth2 Proxy sign-in page"
)

# Validate TESTS array has correct element count
TEST_COUNT=${#TESTS[@]}
if (( TEST_COUNT % 5 != 0 )); then
  echo "Error: TESTS array has ${TEST_COUNT} elements, which is not divisible by 5." >&2
  echo "Each test requires 5 fields: subdomain method path expected_status description" >&2
  exit 2
fi
NUM_TESTS=$(( TEST_COUNT / 5 ))

# ── Test runner ───────────────────────────────────────────────────────────────
TOTAL=0
PASSED=0
FAILED=0
ERRORS=0
FAILED_DETAILS=()

run_test() {
  local protocol="$1"
  local subdomain="$2"
  local method="$3"
  local path="$4"
  local expected="$5"
  local description="$6"

  # Build URL
  local host
  if [[ -n "$subdomain" ]]; then
    host="${subdomain}.${HOSTNAME}"
  else
    host="${HOSTNAME}"
  fi
  local url="${protocol}://${host}${path}"

  TOTAL=$((TOTAL + 1))

  # Run curl — capture status code and redirect URL
  local output
  local exit_code=0
  output=$(curl -s -o /dev/null \
    -w '%{http_code}\n%{redirect_url}' \
    -X "$method" \
    --max-redirs 0 \
    -m "$TIMEOUT" \
    "$url" 2>/dev/null) || exit_code=$?

  # Parse response — line 1 is status code, line 2 is redirect URL (may be empty)
  local status_code redirect_url
  status_code=$(echo "$output" | sed -n '1p')
  redirect_url=$(echo "$output" | sed -n '2p')

  # Handle curl errors (connection refused, timeout, DNS failure, etc.)
  if [[ "$exit_code" -ne 0 && -z "$status_code" ]] || [[ "$status_code" == "000" ]]; then
    ERRORS=$((ERRORS + 1))
    FAILED=$((FAILED + 1))
    local detail
    detail=$(printf "  [ERROR] %-6s %-45s curl error (exit code %d)" "$method" "$description" "$exit_code")
    printf "  ${RED}[ERROR]${RESET} %-6s %-45s curl error (exit code %d)\n" "$method" "$description" "$exit_code"
    FAILED_DETAILS+=("$detail")
    return
  fi

  # Evaluate result
  local pass=false
  local result_msg=""

  # HTTP→HTTPS redirect (301/308): follow the redirect and verify the final status.
  # Traefik upgrades HTTP to HTTPS before any middleware runs.
  if [[ "$protocol" == "http" && ("$status_code" == "301" || "$status_code" == "308") ]]; then
    if echo "$redirect_url" | grep -q "^https://"; then
      # Follow the redirect to verify the HTTPS endpoint also blocks/serves correctly
      local final_code=0
      final_code=$(curl -s -o /dev/null \
        -w '%{http_code}' \
        -X "$method" \
        --max-redirs 0 \
        -m "$TIMEOUT" \
        "$redirect_url" 2>/dev/null) || true

      if [[ "$final_code" == "$expected" ]]; then
        pass=true
        result_msg="${status_code} -> HTTPS -> ${final_code}"
      elif [[ "$final_code" == "200" && "$expected" != "200" ]]; then
        result_msg="${status_code} -> HTTPS -> ${final_code} (SECURITY: unauthenticated access!)"
      else
        result_msg="${status_code} -> HTTPS -> ${final_code} (expected ${expected})"
      fi
    else
      result_msg="${status_code} -> ${redirect_url:-<no location>} (expected HTTPS redirect)"
    fi
  else
    # Direct response — exact status code match
    if [[ "$status_code" == "$expected" ]]; then
      pass=true
      result_msg="${status_code}"
    elif [[ "$status_code" == "200" && "$expected" != "200" ]]; then
      result_msg="${status_code} (SECURITY: unauthenticated access!)"
    else
      result_msg="${status_code} (expected ${expected}, got ${status_code})"
    fi
  fi

  if [[ "$pass" == "true" ]]; then
    PASSED=$((PASSED + 1))
    printf "  ${GREEN}[PASS]${RESET} %-6s %-45s %s\n" "$method" "$description" "$result_msg"
  else
    FAILED=$((FAILED + 1))
    local detail
    detail=$(printf "  [FAIL] %-6s %-45s %s" "$method" "$description" "$result_msg")
    printf "  ${RED}[FAIL]${RESET} %-6s %-45s %s\n" "$method" "$description" "$result_msg"
    FAILED_DETAILS+=("$detail")
  fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
echo ""
printf "${BOLD}AuthZ Tests — Unauthenticated Access${RESET}\n"
printf "Target: %s\n" "$HOSTNAME"
printf "Tests:  %d endpoints x 2 protocols (HTTPS + HTTP) = %d tests\n" "$NUM_TESTS" "$((NUM_TESTS * 2))"
echo "============================================================"

for protocol in https http; do
  echo ""
  printf "${BOLD}%s${RESET}\n" "$(echo "$protocol" | tr '[:lower:]' '[:upper:]')"
  echo "------------------------------------------------------------"

  for (( i=0; i<TEST_COUNT; i+=5 )); do
    run_test "$protocol" \
      "${TESTS[$i]}" \
      "${TESTS[$((i+1))]}" \
      "${TESTS[$((i+2))]}" \
      "${TESTS[$((i+3))]}" \
      "${TESTS[$((i+4))]}"
  done
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
if [[ "$FAILED" -eq 0 ]]; then
  printf "${GREEN}${BOLD}Results: %d/%d passed${RESET}\n" "$PASSED" "$TOTAL"
else
  printf "${RED}${BOLD}Results: %d/%d passed, %d FAILED${RESET}\n" "$PASSED" "$TOTAL" "$FAILED"
  if [[ "$ERRORS" -gt 0 ]]; then
    printf "${YELLOW}(%d connection errors — is Scout running at %s?)${RESET}\n" "$ERRORS" "$HOSTNAME"
  fi
  echo ""
  printf "${RED}Failed tests:${RESET}\n"
  for detail in "${FAILED_DETAILS[@]}"; do
    printf "${RED}%s${RESET}\n" "$detail"
  done
fi
echo ""

if [[ "$FAILED" -gt 0 ]]; then
  exit 1
fi
