#!/usr/bin/env bash
# Data authorization integration tests — ADR 0020 + 0021 + 0022 + 0023.
#
# Runs as a one-shot Kubernetes Job in scout-analytics inside CI's
# smoke-test cluster. Exercises the end-to-end AuthZ pipeline:
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
# Test scenarios (11):
#   1. enabled=true, allowed_facilities=["ABCHOSP1"], allowed_modalities=["*"]
#      -> COUNT(*) FROM test_reports = 3   (only ABCHOSP1 rows)
#   2. allowed_facilities=["*"]
#      -> COUNT(*) FROM test_reports = 6   (all rows)
#   3. allowed_facilities=["ABCHOSP1"], allowed_modalities=["CT"]
#      -> COUNT(*) = 2                     (intersection)
#   4. allowed_facilities=[]  (unset)
#      -> rowFilters emits 1=0 -> COUNT(*) = 0
#   5. mask_phi_fields=["true"], allowed_facilities=["*"]
#      -> patient_name & zip = '[REDACTED]' (varchar); full_patient_name
#         = NULL (non-varchar) -- type-aware mask
#   5b. mask_phi_fields=["false"]
#      -> real values returned (masking is conditional, not unconditional)
#   6. SELECT * FROM reports_report_patient_mapping (no bypass)
#      -> 403 / permission denied
#   7. bypass_hidden_tables=["true"]
#      -> same query succeeds (table missing in CI, query may error on
#         table-not-found but NOT on permission-denied)
#   8. enabled=false
#      -> any SELECT denied at /allow
#   9. not in scout-user group
#      -> any SELECT denied at /allow
#   10. PASSWORD auth + X-Trino-User impersonation (PR6, optional —
#       requires MCP_SVC_PASSWORD env)
#      -> COUNT(*) FROM test_reports = 3   (same as scenario 1 but
#         exercising HTTP Basic instead of Bearer JWT)

set -euo pipefail

KC_URL=${KC_URL:?missing}
KC_REALM=${KC_REALM:-scout}
KC_ADMIN_USERNAME=${KC_ADMIN_USERNAME:-admin}
KC_ADMIN_PASSWORD=${KC_ADMIN_PASSWORD:?missing}
SUPERSET_SVC_CLIENT_ID=${SUPERSET_SVC_CLIENT_ID:-superset_svc}
SUPERSET_SVC_CLIENT_SECRET=${SUPERSET_SVC_CLIENT_SECRET:?missing}
OPA_URL=${OPA_URL:-http://opa-trino.scout-analytics:8181}
TRINO_URL=${TRINO_URL:-https://trino.scout-analytics:8443}
TEST_USER=${TEST_USER:-authz-ci-user}

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
#
# Gotcha: when the user isn't in the bundle yet, OPA returns {} and
# `.result` is null. A predicate that uses `// []` fallbacks (e.g.
# `(.result.x // []) | length == 0`) would then be satisfied by the
# ABSENT user, short-circuiting the wait before propagation actually
# happened. Such predicates must lead with `.result != null and (...)`
# so an empty/absent record never counts as ready.
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
# combined data array on stdout, or "ERROR: ..." on stderr + non-zero
# exit if Trino reports an error. The auth curl args (a Bearer header or
# -u for HTTP Basic) are passed as trailing arguments and reused for the
# POST and every nextUri follow, so the JWT and PASSWORD paths differ only
# in how they authenticate. (trino_query_expect_deny keeps its own loop —
# it inverts this, treating a Trino error as the expected outcome, and
# needs the raw HTTP status, so sharing would obscure both.)
_trino_run() {
    local sql=$1 user=$2
    shift 2
    local auth=("$@")
    local next response data page_data err

    response=$(curl -fsS "${CURL_CA_OPTS[@]}" -X POST "$TRINO_URL/v1/statement" \
        "${auth[@]}" \
        -H "X-Trino-User: $user" \
        -H "X-Trino-Catalog: delta" \
        -H "X-Trino-Schema: default" \
        --data-binary "$sql")

    data='[]'
    while true; do
        page_data=$(echo "$response" | jq -c '.data // []')
        if [[ "$page_data" != "[]" && "$page_data" != "null" ]]; then
            data=$(jq -cn --argjson a "$data" --argjson b "$page_data" '$a + $b')
        fi
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
            "${auth[@]}" \
            -H "X-Trino-User: $user")
    done
}

# Submit via Bearer JWT (superset_svc) — the default path; X-Trino-User
# names the impersonated end-user for OPA. SQL as $1, Trino-User as $2.
trino_query() {
    local sql=$1 user=$2
    local token
    token=$(get_svc_token)
    _trino_run "$sql" "$user" -H "Authorization: Bearer $token"
}

# Submit via HTTP Basic against Trino's PASSWORD authenticator (ADR 0022) —
# the path mcp-trino uses against the dual-auth listener (its outbound is
# HTTP-Basic-only). Exercises the PASSWORD path end to end: bcrypt match in
# password.db, OPA impersonation allow, row filter, Delta connector.
trino_query_via_password() {
    local sql=$1 user=$2
    _trino_run "$sql" "$user" -u "openwebui_mcp_svc:$MCP_SVC_PASSWORD"
}

# Variant that expects the query to fail (deny). Returns 0 if Trino
# refused (HTTP error or error in response body), 1 if the query
# succeeded unexpectedly.
trino_query_expect_deny() {
    local sql=$1 user=$2
    local token http_code response next
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
    # 200 OK on the POST is the normal case -- Trino doesn't decide
    # access until the query has been planned, which happens on the
    # second or later nextUri hop. Drain the chain looking for the
    # `.error.message` Trino synthesizes when OPA denies. (Single-hop
    # follow misses denials that surface after queued -> planning
    # transitions.)
    while true; do
        if echo "$response" | jq -e '.error' >/dev/null 2>&1; then
            return 0
        fi
        next=$(echo "$response" | jq -r '.nextUri // empty')
        if [[ -z "$next" ]]; then
            return 1
        fi
        response=$(curl -sS "${CURL_CA_OPTS[@]}" "$next" \
            -H "Authorization: Bearer $token" \
            -H "X-Trino-User: $user")
    done
}

# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------

USER_ID=$(ensure_test_user)
log "test user: $TEST_USER (id=$USER_ID)"

# Bundle propagation can take ~10-15s on first event after a fresh
# Keycloak start (the SPI's postInit walks the realm, debounces 1s,
# uploads, then OPA pulls at next polling tick — 5-10s default).
PROPAGATION_TIMEOUT=30

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

# Gate the scenarios on scout-user membership reaching OPA. Without
# this each scenario's own wait_for_bundle only confirms its
# attributes landed -- if the SPI happens to publish a bundle in the
# narrow window between the GROUP_MEMBERSHIP event and the
# subsequent UPDATE_USER event, OPA sees the new attrs against a
# user record whose `groups` is still empty (or being repopulated),
# the rego's user_in_approved_group check fails, and every query
# below gets `Access Denied: Cannot execute query` even though the
# attribute predicate has passed.
wait_for_bundle '(.result.groups // []) | index("scout-user") != null' "$PROPAGATION_TIMEOUT"

# Close the race between Spark's CREATE TABLE in the seed Job and
# Trino's Delta connector loading the new snapshot. The very first
# SELECT against a freshly-created Delta table can return
# DELTA_LAKE_INVALID_SCHEMA ("Error getting snapshot for ...") for a
# few seconds while the _delta_log directory listing and Hive
# get_table calls overlap. Issue one warmup probe here and retry
# until Trino returns rows -- rowFilter clamps the result with the
# user's current (no-facilities) attrs, but that's fine; we only
# care that the snapshot load itself succeeds. Bounded; if Trino is
# genuinely broken the scenarios surface the real error.
warmup_trino_snapshot() {
    local i=0 last_err=""
    while [[ $i -lt 12 ]]; do
        if trino_query "SELECT 1 FROM test_reports LIMIT 1" "$TEST_USER" >/dev/null 2>/tmp/warmup_err; then
            [[ $i -gt 0 ]] && log "Trino snapshot ready after $((i + 1)) attempts"
            return 0
        fi
        last_err=$(cat /tmp/warmup_err)
        if [[ "$last_err" != *"Error getting snapshot"* ]]; then
            # Not the race we're targeting -- let scenario 1 surface it.
            log "warmup: non-snapshot error, continuing to scenarios: $last_err"
            return 0
        fi
        i=$((i + 1))
        sleep 5
    done
    log "WARN: Trino snapshot still failing after $i attempts: $last_err"
}
warmup_trino_snapshot

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
wait_for_bundle '.result != null and (.result.allowed_facilities // [] | length) == 0' "$PROPAGATION_TIMEOUT"
count=$(trino_query "SELECT COUNT(*) AS c FROM test_reports" "$TEST_USER" | jq -r '.[0][0]')
if [[ "$count" == "0" ]]; then ok "row count=0 (1=0 clamp)"; else fail "expected 0, got $count"; fi

# --- Scenario 5: PHI masking ON ---------------------------------------------
# Type-aware mask (ADR 0020): varchar PHI -> literal '[REDACTED]'; non-varchar
# PHI (full_patient_name struct) -> NULL. All three columns are in
# trino_masked_columns, so all three must mask under mask_phi_fields=true.
log "scenario 5: mask_phi_fields=true -> PHI columns masked"
set_user_state "$USER_ID" true '{"allowed_facilities":["*"],"allowed_modalities":["*"],"mask_phi_fields":["true"]}'
wait_for_bundle '.result.mask_phi_fields[0] == "true"' "$PROPAGATION_TIMEOUT"
row=$(trino_query "SELECT patient_name, zip_or_postal_code, full_patient_name FROM test_reports ORDER BY patient_name LIMIT 1" "$TEST_USER")
m_name=$(echo "$row" | jq -r '.[0][0]')
m_zip=$(echo "$row" | jq -r '.[0][1]')
m_struct=$(echo "$row" | jq -r '.[0][2]')
if [[ "$m_name" == "[REDACTED]" ]]; then ok "patient_name=[REDACTED] (varchar mask)"; else fail "expected [REDACTED], got: $m_name"; fi
if [[ "$m_zip" == "[REDACTED]" ]]; then ok "zip_or_postal_code=[REDACTED] (varchar mask)"; else fail "expected [REDACTED] zip, got: $m_zip"; fi
if [[ "$m_struct" == "null" ]]; then ok "full_patient_name=NULL (non-varchar mask)"; else fail "expected NULL struct, got: $m_struct"; fi

# --- Scenario 5b: PHI masking OFF (counter-assertion) -----------------------
# Proves masking is *conditional*, not unconditional: with mask_phi_fields
# explicitly "false" the real values come back. Without this, a policy that
# redacted these columns for everyone would pass scenario 5 just as well.
# ORDER BY pins a deterministic row (Alice Anderson) under the wildcard filter.
log "scenario 5b: mask_phi_fields=false -> real PHI values returned"
set_user_state "$USER_ID" true '{"allowed_facilities":["*"],"allowed_modalities":["*"],"mask_phi_fields":["false"]}'
wait_for_bundle '.result.mask_phi_fields[0] == "false"' "$PROPAGATION_TIMEOUT"
row=$(trino_query "SELECT patient_name, zip_or_postal_code FROM test_reports ORDER BY patient_name LIMIT 1" "$TEST_USER")
real_name=$(echo "$row" | jq -r '.[0][0]')
real_zip=$(echo "$row" | jq -r '.[0][1]')
if [[ "$real_name" == "Alice Anderson" ]]; then ok "patient_name=Alice Anderson (unmasked)"; else fail "expected Alice Anderson, got: $real_name"; fi
if [[ "$real_zip" == "63110" ]]; then ok "zip_or_postal_code=63110 (unmasked)"; else fail "expected 63110, got: $real_zip"; fi

# --- Scenario 6: direct SELECT on hidden mapping table denied ---------------
log "scenario 6: hidden table permission-denied without bypass"
# reports_report_patient_mapping is seeded (seed.py) AND hardcoded as a
# baseline hidden table in the rego. Because the table exists, a refusal here
# is a genuine OPA permission denial, not an incidental TABLE_NOT_FOUND —
# assert PERMISSION_DENIED specifically so the test can't pass for the wrong
# reason. (The prior scenario leaves the user wildcard-but-no-bypass.)
err=$(trino_query "SELECT * FROM reports_report_patient_mapping LIMIT 1" "$TEST_USER" 2>&1 || true)
if echo "$err" | grep -qE "PERMISSION_DENIED|Access Denied"; then
    ok "hidden table permission-denied without bypass"
else
    fail "expected PERMISSION_DENIED on hidden table, got: $err"
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
wait_for_bundle '.result != null and ((.result.groups // []) | index("scout-user") == null)' "$PROPAGATION_TIMEOUT"
if trino_query_expect_deny "SELECT 1" "$TEST_USER"; then
    ok "user without scout-user denied"
else
    fail "unapproved user query unexpectedly succeeded"
fi
# Restore membership so subsequent test runs against the same Keycloak
# instance start from a clean state.
add_user_to_group "$USER_ID" "$SCOUT_USER_GROUP_ID"

# --- Scenario 10: PASSWORD auth + impersonation (PR6) -----------------------
# Exercises the dual-auth listener's PASSWORD path that mcp-trino uses
# (HTTP Basic + X-Trino-User), end to end: bcrypt match in password.db,
# OPA permits openwebui_mcp_svc to impersonate, row filter applies.
# Skipped if MCP_SVC_PASSWORD isn't injected (deploys without the
# password-authenticator Secret task running, e.g. trino role pre-PR6).
if [[ -n "${MCP_SVC_PASSWORD:-}" ]]; then
    log "scenario 10: PASSWORD-auth + X-Trino-User -> 3 ABCHOSP1 rows"
    set_user_state "$USER_ID" true '{"allowed_facilities":["ABCHOSP1"],"allowed_modalities":["*"]}'
    wait_for_bundle '.result.allowed_facilities[0] == "ABCHOSP1"' "$PROPAGATION_TIMEOUT"
    count=$(trino_query_via_password "SELECT COUNT(*) AS c FROM test_reports" "$TEST_USER" | jq -r '.[0][0]')
    if [[ "$count" == "3" ]]; then
        ok "PASSWORD+impersonation: row filter sees 3 ABCHOSP1 rows"
    else
        fail "PASSWORD+impersonation expected 3 rows, got $count"
    fi
else
    log "scenario 10 skipped: MCP_SVC_PASSWORD not set"
fi

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
log "cleanup: deleting test user"
curl -fsS -o /dev/null -X DELETE \
    "$KC_URL/admin/realms/$KC_REALM/users/$USER_ID" \
    -H "Authorization: Bearer $(get_admin_token)" || true

log "summary: $PASS passed, $FAIL failed"
exit $((FAIL > 0 ? 1 : 0))
