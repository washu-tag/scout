#!/usr/bin/env bash
# CI-only auth leg. Run from the repository root after its Flux nodes are Ready.
# Requires installed npm dependencies + Chromium, trusted ingress CA, and the
# already verified CONFIG_SOURCE@CONFIG_DIGEST. The optional data-authz argument
# is also used to give job failures their own fail-fast shell below.
set -euo pipefail
: "${RUNNER_TEMP:?}"
script_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$script_dir/../../.."

case "${1:-all}" in
  all)
    export NODE_EXTRA_CA_CERTS="${NODE_EXTRA_CA_CERTS:-${PLATFORM_CA_CERT:?}}"
    [ -s "$NODE_EXTRA_CA_CERTS" ] || { echo "::error::ingress CA is missing"; exit 1; }
    # Browser and Node must both use the CA installed by bootstrap_auth.sh.
    export PLAYWRIGHT_IGNORE_HTTPS_ERRORS=false
    export SCOUT_HOSTNAME=scout.test KEYCLOAK_ADMIN_USER=admin
    export UNAUTHORIZED_USER_USERNAME=scout-unauthorized-flux-ci-user
    export AUTHORIZED_USER_USERNAME=scout-authorized-flux-ci-user
    export TEST_USER_PASSWORD=ci-test-user-password-1234
    KEYCLOAK_ADMIN_PASSWORD="$(jq -er '.keycloak_bootstrap_admin_password | select(type == "string" and length > 0)' \
      .github/ci_resources/flux/secret-values.json)"
    export KEYCLOAK_ADMIN_PASSWORD
    rc=0
    # Report browser and data failures independently, then fail the whole proof.
    bash tests/auth/auth-curl-tests.sh "$SCOUT_HOSTNAME" --include keycloak,auth || rc=1
    (cd tests/auth && npx --no-install playwright test flux-platform.spec.ts) || rc=1
    bash "$script_dir/run_auth_tests.sh" data-authz || rc=1
    exit "$rc"
    ;;
  data-authz)
    : "${CONFIG_SOURCE:?}" "${CONFIG_DIGEST:?}" "${CONFIG_INSECURE:?}"
    if ! [[ "$CONFIG_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
      echo "::error::CONFIG_DIGEST must be an immutable sha256 digest"
      exit 1
    fi
    oras_args=(pull)
    case "$CONFIG_INSECURE" in
      true) oras_args+=(--plain-http) ;;
      false) ;;
      *) echo "::error::CONFIG_INSECURE must be true or false"; exit 1 ;;
    esac
    umask 077
    work="$(mktemp -d "$RUNNER_TEMP/flux-auth.XXXXXX")"
    trap 'rm -rf "$work"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    # Both artifact modes take this same path. No mutable tag or checkout image.
    oras "${oras_args[@]}" "${CONFIG_SOURCE}@${CONFIG_DIGEST}" -o "$work"
    python3 "$script_dir/auth_jobs.py" "$work/scout-config.tar.gz" "$work/jobs" \
      "$RUNNER_TEMP/cluster-vars.values.json"
    kubectl -n scout-data create configmap data-authz-seed-script \
      --from-file=seed.py=tests/data-authorization/seed/seed.py \
      --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n scout-data delete job data-authz-seed --ignore-not-found --wait=true
    kubectl apply -f "$work/jobs/seed.json"
    bash tests/data-authorization/wait-for-job.sh scout-data data-authz-seed 300

    kubectl -n scout-analytics create configmap data-authz-test-script \
      --from-file=run.sh=tests/data-authorization/authz-tests/run.sh \
      --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n scout-analytics create secret generic data-authz-test-creds \
      --from-file="KC_ADMIN_PASSWORD=$work/jobs/KC_ADMIN_PASSWORD" \
      --from-file="SUPERSET_SVC_CLIENT_SECRET=$work/jobs/SUPERSET_SVC_CLIENT_SECRET" \
      --from-file="REPORT_VIEWER_SVC_CLIENT_SECRET=$work/jobs/REPORT_VIEWER_SVC_CLIENT_SECRET" \
      --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n scout-analytics delete job data-authz-tests --ignore-not-found --wait=true
    kubectl apply -f tests/data-authorization/authz-tests/job.yaml
    bash tests/data-authorization/wait-for-job.sh scout-analytics data-authz-tests 300
    ;;
  *) echo "usage: $0 [all|data-authz]" >&2; exit 2 ;;
esac
