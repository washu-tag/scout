#!/usr/bin/env bash
# Data authorization integration tests — ADR 0020 + 0021 + 0022 + 0023.
#
# Runs as a one-shot Kubernetes Job in scout-analytics inside CI's
# smoke-test cluster. Exercises the end-to-end RBAC pipeline:
#
#   Keycloak admin API (set user attributes)
#     -> OPA bundle publisher SPI (catches admin event)
#     -> MinIO bundle (writes tar.gz)
#     -> OPA bundle plugin (pulls, swaps data.users)
#     -> Trino /v1/statement (JWT-authenticated, X-Trino-User impersonated)
#     -> Result rows reflect the row filter / column mask the rego emits
#
# Inputs (env):
#   KC_URL                       in-cluster Keycloak base URL
#   KC_REALM                     scout
#   KC_ADMIN_USERNAME            admin
#   KC_ADMIN_PASSWORD            from inventory
#   SUPERSET_SVC_CLIENT_ID       superset_svc
#   SUPERSET_SVC_CLIENT_SECRET   from inventory
#   OPA_URL                      http://opa-trino.scout-analytics:8181
#   TRINO_URL                    https://trino.scout-analytics:8443
#   TRINO_CA_BUNDLE              optional path to a CA bundle for TLS
#                                verification; omitted -> curl -k
#   TEST_USER                    username to manipulate (created on the fly)
#
# Output:
#   stdout: a streaming log of each scenario + assertion
#   exit:   0 if all pass, non-zero on first failure
#
# Test scenarios (9):
#   1. enabled=true, allowed_facilities=["ABCHOSP1"], allowed_modalities=["*"]
#      -> COUNT(*) FROM test_reports = 3   (only ABCHOSP1 rows)
#   2. allowed_facilities=["*"]
#      -> COUNT(*) FROM test_reports = 6   (all rows)
#   3. allowed_facilities=["ABCHOSP1"], allowed_modalities=["CT"]
#      -> COUNT(*) = 2                     (intersection)
#   4. allowed_facilities=[]  (unset)
#      -> rowFilters emits 1=0 -> COUNT(*) = 0
#   5. mask_phi_fields=["true"], allowed_facilities=["*"]
#      -> SELECT patient_name LIMIT 1 = '[REDACTED]'
#   6. SELECT * FROM reports_report_patient_mapping (no bypass)
#      -> 403 / permission denied
#   7. bypass_hidden_tables=["true"]
#      -> same query succeeds (table missing in CI, query may error on
#         table-not-found but NOT on permission-denied)
#   8. enabled=false
#      -> any SELECT denied at /allow

set -euo pipefail

KC_URL=${KC_URL:?missing}
KC_REALM=${KC_REALM:-scout}
KC_ADMIN_USERNAME=${KC_ADMIN_USERNAME:-admin}
KC_ADMIN_PASSWORD=${KC_ADMIN_PASSWORD:?missing}
SUPERSET_SVC_CLIENT_ID=${SUPERSET_SVC_CLIENT_ID:-superset_svc}
SUPERSET_SVC_CLIENT_SECRET=${SUPERSET_SVC_CLIENT_SECRET:?missing}
OPA_URL=${OPA_URL:-http://opa-trino.scout-analytics:8181}
TRINO_URL=${TRINO_URL:-https://trino.scout-analytics:8443}
TEST_USER=${TEST_USER:-rbac-ci-user}

CURL_CA_OPTS=()
if [[ -n "${TRINO_CA_BUNDLE:-}" ]]; then
    CURL_CA_OPTS=(--cacert "$TRINO_CA_BUNDLE")
else
    # CI uses a cert-manager-issued cert whose CA isn't in the curl
    # image's default trust store. Skip verification for the in-cluster
    # call — we're hitting trino.scout-analytics directly over the
    # cluster network, not an externally reachable URL, so MITM isn't
    # a real risk here.
    CURL_CA_OPTS=(-k)
fi

PASS=0
FAIL=0

log()    { echo "[$(date -u +%H:%M:%S)] $*"; }
ok()     { log "  PASS: $*"; PASS=$((PASS + 1)); }
fail()   { log "  FAIL: $*"; FAIL=$((FAIL + 1)); }

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

# Get a Keycloak master-realm admin token via the admin-cli client.
# Cached for the script's lifetime; admin actions tend to fit inside
# the default 60s access-token lifetime.
get_admin_token() {
    if [[ -z "${ADMIN_TOKEN:-}" ]]; then
        ADMIN_TOKEN=$(curl -fsS -X POST \
            "$KC_URL/realms/master/protocol/openid-connect/token" \
            -d "grant_type=password" \
            -d "client_id=admin-cli" \
            -d "username=$KC_ADMIN_USERNAME" \
            --data-urlencode "password=$KC_ADMIN_PASSWORD" \
            | jq -r .access_token)
        if [[ -z "$ADMIN_TOKEN" || "$ADMIN_TOKEN" == "null" ]]; then
            log "FATAL: could not obtain Keycloak admin token"
            exit 2
        fi
    fi
    echo "$ADMIN_TOKEN"
}

# Get a superset_svc client_credentials token. This is the service-
# principal JWT Trino accepts; X-Trino-User then names the impersonated
# end-user for OPA decisions.
get_svc_token() {
    if [[ -z "${SVC_TOKEN:-}" ]]; then
        SVC_TOKEN=$(curl -fsS -X POST \
            "$KC_URL/realms/$KC_REALM/protocol/openid-connect/token" \
            -d "grant_type=client_credentials" \
            -d "client_id=$SUPERSET_SVC_CLIENT_ID" \
            --data-urlencode "client_secret=$SUPERSET_SVC_CLIENT_SECRET" \
            | jq -r .access_token)
        if [[ -z "$SVC_TOKEN" || "$SVC_TOKEN" == "null" ]]; then
            log "FATAL: could not obtain superset_svc token"
            exit 2
        fi
    fi
    echo "$SVC_TOKEN"
}

# ---------------------------------------------------------------------------
# Keycloak user manipulation
# ---------------------------------------------------------------------------

ensure_test_user() {
    local token user_id
    token=$(get_admin_token)
    # idempotent create — 409 means user already exists
    curl -sS -o /dev/null -w "%{http_code}" -X POST \
        "$KC_URL/admin/realms/$KC_REALM/users" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"$TEST_USER\",\"enabled\":true,\"emailVerified\":true,\"email\":\"$TEST_USER@example.test\"}" \
        > /tmp/create_status
    case "$(cat /tmp/create_status)" in
        201|409) ;;
        *) log "FATAL: unexpected status creating user: $(cat /tmp/create_status)"; exit 2 ;;
    esac
    user_id=$(curl -fsS \
        "$KC_URL/admin/realms/$KC_REALM/users?username=$TEST_USER&exact=true" \
        -H "Authorization: Bearer $token" \
        | jq -r '.[0].id')
    if [[ -z "$user_id" || "$user_id" == "null" ]]; then
        log "FATAL: could not resolve user id for $TEST_USER"
        exit 2
    fi
    echo "$user_id"
}

# Update the user's attributes + enabled flag in one PUT. Keycloak's
# user-update is total replacement of the attributes block, so
# anything not in the payload here is cleared — that's the intent
# (each scenario sets exactly the attrs it wants).
set_user_state() {
    local user_id=$1 enabled=$2 attrs_json=$3
    local token
    token=$(get_admin_token)
    curl -fsS -o /dev/null -X PUT \
        "$KC_URL/admin/realms/$KC_REALM/users/$user_id" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{\"enabled\":$enabled,\"attributes\":$attrs_json}"
}

# Look up the Keycloak group id for a given group name. Returns empty
# string if the group doesn't exist.
get_group_id() {
    local group_name=$1
    local token
    token=$(get_admin_token)
    curl -fsS \
        "$KC_URL/admin/realms/$KC_REALM/groups?search=$group_name&exact=true" \
        -H "Authorization: Bearer $token" \
        | jq -r '.[0].id // ""'
}

# Add the user to a group (idempotent — Keycloak returns 204 either way).
add_user_to_group() {
    local user_id=$1 group_id=$2
    local token
    token=$(get_admin_token)
    curl -fsS -o /dev/null -X PUT \
        "$KC_URL/admin/realms/$KC_REALM/users/$user_id/groups/$group_id" \
        -H "Authorization: Bearer $token"
}

# Remove the user from a group (idempotent).
remove_user_from_group() {
    local user_id=$1 group_id=$2
    local token
    token=$(get_admin_token)
    curl -fsS -o /dev/null -X DELETE \
        "$KC_URL/admin/realms/$KC_REALM/users/$user_id/groups/$group_id" \
        -H "Authorization: Bearer $token"
}

# ---------------------------------------------------------------------------
# Bundle propagation gate
# ---------------------------------------------------------------------------

# Poll OPA's data.users.<user> until the predicate returns success or
# the timeout elapses. The predicate is a jq filter applied to the
# user's record; success is jq returning non-null/non-false.
wait_for_bundle() {
    local predicate=$1 timeout=${2:-30}
    local end=$(( $(date +%s) + timeout ))
    while [[ $(date +%s) -lt $end ]]; do
        local body matches
        body=$(curl -fsS "$OPA_URL/v1/data/users/$TEST_USER" || echo '{}')
        matches=$(echo "$body" | jq "$predicate" 2>/dev/null)
        if [[ -n "$matches" && "$matches" != "null" && "$matches" != "false" ]]; then
            return 0
        fi
        sleep 1
    done
    log "  TIMEOUT after ${timeout}s waiting for bundle predicate: $predicate"
    log "  last OPA response: $(curl -fsS "$OPA_URL/v1/data/users/$TEST_USER" || echo unreachable)"
    return 1
}

# ---------------------------------------------------------------------------
# Trino query helpers
# ---------------------------------------------------------------------------

# Submit a SQL statement and follow nextUri until done. Returns the
# combined data array on stdout, or empty + non-zero exit on error.
# Caller passes the SQL as $1 and Trino-User as $2.
trino_query() {
    local sql=$1 user=$2
    local token next response data
    token=$(get_svc_token)

    response=$(curl -fsS "${CURL_CA_OPTS[@]}" -X POST "$TRINO_URL/v1/statement" \
        -H "Authorization: Bearer $token" \
        -H "X-Trino-User: $user" \
        -H "X-Trino-Catalog: delta" \
        -H "X-Trino-Schema: default" \
        --data-binary "$sql")

    data='[]'
    while true; do
        # Pull rows from this page (if any)
        local page_data
        page_data=$(echo "$response" | jq -c '.data // []')
        if [[ "$page_data" != "[]" && "$page_data" != "null" ]]; then
            data=$(jq -cn --argjson a "$data" --argjson b "$page_data" '$a + $b')
        fi
        # Check for error before following nextUri
        local err
        err=$(echo "$response" | jq -r '.error.message // empty')
        if [[ -n "$err" ]]; then
            echo "ERROR: $err" >&2
            return 1
        fi
        next=$(echo "$response" | jq -r '.nextUri // empty')
        if [[ -z "$next" ]]; then
            echo "$data"
            return 0
        fi
        response=$(curl -fsS "${CURL_CA_OPTS[@]}" "$next" \
            -H "Authorization: Bearer $token" \
            -H "X-Trino-User: $user")
    done
}

# Variant that expects the query to fail (deny). Returns 0 if Trino
# refused (HTTP error or error in response body), 1 if the query
# succeeded unexpectedly.
trino_query_expect_deny() {
    local sql=$1 user=$2
    local token http_code response
    token=$(get_svc_token)

    response=$(curl -sS "${CURL_CA_OPTS[@]}" -X POST "$TRINO_URL/v1/statement" \
        -H "Authorization: Bearer $token" \
        -H "X-Trino-User: $user" \
        -H "X-Trino-Catalog: delta" \
        -H "X-Trino-Schema: default" \
        --data-binary "$sql" -w "\n%{http_code}")
    http_code=$(echo "$response" | tail -n1)
    response=$(echo "$response" | head -n -1)

    if [[ "$http_code" =~ ^4 || "$http_code" =~ ^5 ]]; then
        return 0
    fi
    # Trino returns 200 with an error body when the query parses but
    # access-control denies. Follow nextUri once to surface the error.
    local next
    next=$(echo "$response" | jq -r '.nextUri // empty')
    if [[ -n "$next" ]]; then
        response=$(curl -sS "${CURL_CA_OPTS[@]}" "$next" \
            -H "Authorization: Bearer $token" \
            -H "X-Trino-User: $user")
    fi
    if echo "$response" | jq -e '.error' >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------

USER_ID=$(ensure_test_user)
log "test user: $TEST_USER (id=$USER_ID)"

# Look up scout-user group id and put the test user in it. The OPA
# `user_enabled` gate requires membership in an approved group; without
# this every scenario below would fail with /allow-denied even though
# their attributes are configured correctly.
SCOUT_USER_GROUP_ID=$(get_group_id scout-user)
if [[ -z "$SCOUT_USER_GROUP_ID" ]]; then
    log "FATAL: scout-user group not found in realm $KC_REALM"
    exit 2
fi
add_user_to_group "$USER_ID" "$SCOUT_USER_GROUP_ID"
log "added $TEST_USER to scout-user (group id=$SCOUT_USER_GROUP_ID)"

# Bundle propagation can take ~10-15s on first event after a fresh
# Keycloak start (the SPI's postInit walks the realm, debounces 1s,
# uploads, then OPA pulls at next polling tick — 5-10s default).
PROPAGATION_TIMEOUT=30

# --- Scenario 1: single facility filter -------------------------------------
log "scenario 1: allowed_facilities=[ABCHOSP1] -> 3 rows"
set_user_state "$USER_ID" true '{"allowed_facilities":["ABCHOSP1"],"allowed_modalities":["*"]}'
wait_for_bundle '.result.allowed_facilities[0] == "ABCHOSP1" and .result.enabled == true' "$PROPAGATION_TIMEOUT"
count=$(trino_query "SELECT COUNT(*) AS c FROM test_reports" "$TEST_USER" | jq -r '.[0][0]')
if [[ "$count" == "3" ]]; then ok "row count=3"; else fail "expected 3, got $count"; fi

# --- Scenario 2: wildcard facility ------------------------------------------
log "scenario 2: allowed_facilities=[*] -> 6 rows"
set_user_state "$USER_ID" true '{"allowed_facilities":["*"],"allowed_modalities":["*"]}'
wait_for_bundle '.result.allowed_facilities[0] == "*"' "$PROPAGATION_TIMEOUT"
count=$(trino_query "SELECT COUNT(*) AS c FROM test_reports" "$TEST_USER" | jq -r '.[0][0]')
if [[ "$count" == "6" ]]; then ok "row count=6"; else fail "expected 6, got $count"; fi

# --- Scenario 3: intersection of two dimensions -----------------------------
log "scenario 3: facility=ABCHOSP1 AND modality=CT -> 2 rows"
set_user_state "$USER_ID" true '{"allowed_facilities":["ABCHOSP1"],"allowed_modalities":["CT"]}'
wait_for_bundle '.result.allowed_modalities[0] == "CT"' "$PROPAGATION_TIMEOUT"
count=$(trino_query "SELECT COUNT(*) AS c FROM test_reports" "$TEST_USER" | jq -r '.[0][0]')
if [[ "$count" == "2" ]]; then ok "row count=2"; else fail "expected 2, got $count"; fi

# --- Scenario 4: unset facilities -> deny-all (1=0 clamp) -------------------
log "scenario 4: no attributes -> deny-all rows"
set_user_state "$USER_ID" true '{}'
wait_for_bundle '.result.allowed_facilities // null | length // 0 == 0' "$PROPAGATION_TIMEOUT"
count=$(trino_query "SELECT COUNT(*) AS c FROM test_reports" "$TEST_USER" | jq -r '.[0][0]')
if [[ "$count" == "0" ]]; then ok "row count=0 (1=0 clamp)"; else fail "expected 0, got $count"; fi

# --- Scenario 5: PHI masking ------------------------------------------------
log "scenario 5: mask_phi_fields=true -> patient_name replaced"
set_user_state "$USER_ID" true '{"allowed_facilities":["*"],"allowed_modalities":["*"],"mask_phi_fields":["true"]}'
wait_for_bundle '.result.mask_phi_fields[0] == "true"' "$PROPAGATION_TIMEOUT"
masked=$(trino_query "SELECT patient_name FROM test_reports LIMIT 1" "$TEST_USER" | jq -r '.[0][0]')
if [[ "$masked" == "[REDACTED]" ]]; then ok "patient_name=[REDACTED]"; else fail "expected [REDACTED], got: $masked"; fi

# --- Scenario 6: direct SELECT on view-only mapping table denied ------------
log "scenario 6: view-only table denied without bypass"
if trino_query_expect_deny "SELECT * FROM reports_report_patient_mapping LIMIT 1" "$TEST_USER"; then
    ok "view-only table denied"
else
    fail "view-only table query unexpectedly succeeded"
fi

# --- Scenario 7: bypass_hidden_tables unlocks ----------------------------
log "scenario 7: bypass_hidden_tables=true -> not /allow-denied"
set_user_state "$USER_ID" true '{"allowed_facilities":["*"],"allowed_modalities":["*"],"bypass_hidden_tables":["true"]}'
wait_for_bundle '.result.bypass_hidden_tables[0] == "true"' "$PROPAGATION_TIMEOUT"
# The table may not exist in CI (no extractor) — Trino will return
# TABLE_NOT_FOUND, NOT PERMISSION_DENIED. Distinguishing the two
# proves the OPA gate was passed.
err=$(trino_query "SELECT * FROM reports_report_patient_mapping LIMIT 1" "$TEST_USER" 2>&1 || true)
if echo "$err" | grep -qE "TABLE_NOT_FOUND|does not exist|line 1:15"; then
    ok "bypass allowed past /allow (table-not-found, not perm-denied)"
elif echo "$err" | grep -qE "PERMISSION_DENIED|Access Denied"; then
    fail "still permission denied with bypass=true"
else
    ok "query succeeded (bypass allowed past /allow)"
fi

# --- Scenario 8: disabled user denied at /allow -----------------------------
log "scenario 8: enabled=false -> all queries denied"
set_user_state "$USER_ID" false '{"allowed_facilities":["*"]}'
wait_for_bundle '.result.enabled == false' "$PROPAGATION_TIMEOUT"
if trino_query_expect_deny "SELECT 1" "$TEST_USER"; then
    ok "disabled user denied"
else
    fail "disabled user query unexpectedly succeeded"
fi

# --- Scenario 9: user removed from scout-user denied at /allow --------------
log "scenario 9: not in scout-user -> all queries denied"
# Re-enable + restore wildcard attrs so the gate's enabled-and-attributes
# half is satisfied; the test isolates the group-membership half.
set_user_state "$USER_ID" true '{"allowed_facilities":["*"],"allowed_modalities":["*"]}'
remove_user_from_group "$USER_ID" "$SCOUT_USER_GROUP_ID"
# Bundle reflects the group change as an empty "groups" list (or one
# without scout-user). The wait_for_bundle predicate succeeds when
# scout-user is no longer present.
wait_for_bundle '(.result.groups // []) | index("scout-user") == null' "$PROPAGATION_TIMEOUT"
if trino_query_expect_deny "SELECT 1" "$TEST_USER"; then
    ok "user without scout-user denied"
else
    fail "unapproved user query unexpectedly succeeded"
fi
# Restore membership so subsequent test runs against the same Keycloak
# instance start from a clean state.
add_user_to_group "$USER_ID" "$SCOUT_USER_GROUP_ID"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
log "cleanup: deleting test user"
curl -fsS -o /dev/null -X DELETE \
    "$KC_URL/admin/realms/$KC_REALM/users/$USER_ID" \
    -H "Authorization: Bearer $(get_admin_token)" || true

log "summary: $PASS passed, $FAIL failed"
exit $((FAIL > 0 ? 1 : 0))
