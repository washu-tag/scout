#!/usr/bin/env bash
# Put a dev XNAT into (or out of) REST-test mode, for running the XNAT REST test suite
# (https://github.com/NrgXnat/xnat-rest-tests) against it. See
# docs/internal/xnat-develop-testing.md.
#
#   on    scale to 1 replica, disable the SSO auto-login redirect, point siteUrl at the
#         forward, restart, and open port-forwards for REST (8080) and DICOM (8104)
#   off   restore auto-login, siteUrl, and the previous replica count; stop the forwards
#
# Why each half is needed: the suite's client (grxnat) probes GET /app/template/Login.vm
# and requires a 200. With openid.<provider>.autoLogin=true that endpoint 302s into the
# SSO flow; the client follows the redirect to the site URL, where oauth2-proxy's
# forwardAuth answers 401 before XNAT sees it, and the suite reports "There doesn't seem
# to be an XNAT reachable at that address." Port-forwarding bypasses the ingress (basic
# auth never survives it). XNAT also builds redirect targets from siteUrl, so a client
# that follows one leaves the forward and hits the same 401 even when the operation
# succeeded. A single replica keeps the suite's JSESSIONID on one pod — there is no
# shared session store, and the sticky cookie that normally covers that lives at Traefik.
#
# Dev clusters only: this edits a live Secret and scales the StatefulSet.

set -euo pipefail

MODE=''
NAMESPACE="${XNAT_NAMESPACE:-xnat}"
CONTEXT="${XNAT_CONTEXT:-}"
RELEASE="${XNAT_RELEASE:-xnat}"

REST_PORT=8080
DICOM_PORT=8104
REPLICAS_ANNOTATION='scout.xnat.org/pre-test-mode-replicas'
SITEURL_ANNOTATION='scout.xnat.org/pre-test-mode-siteurl'
PID_FILE="${TMPDIR:-/tmp}/xnat-test-mode-${NAMESPACE}.pids"

usage() {
    cat >&2 <<EOF
Usage: $(basename "$0") <on|off> --context <kube-context> [--namespace <ns>]

  on    1 replica, SSO auto-login off, port-forwards on ${REST_PORT} (REST) and ${DICOM_PORT} (DICOM)
  off   restore auto-login + siteUrl + replica count, stop the port-forwards

  -c, --context     kube context to target (required — this scales a StatefulSet and
                    patches a Secret, so it is never inferred from your current context)
  -n, --namespace   namespace (default: ${NAMESPACE})

Also settable as XNAT_CONTEXT / XNAT_NAMESPACE / XNAT_RELEASE.

Dev clusters only. A normal 'make install-xnat' undoes everything 'on' changes: the
Secret is rendered from inventory and the replica count comes from chart values.

Example:
  $(basename "$0") on --context <dev-context>
EOF
}

need_value() { [[ $# -ge 2 && -n "${2:-}" ]] || { echo "! $1 needs a value" >&2; usage; exit 2; }; }

while [[ $# -gt 0 ]]; do
    case "$1" in
    on | off) MODE="$1" && shift ;;
    -c | --context) need_value "$@" && CONTEXT="$2" && shift 2 ;;
    -n | --namespace) need_value "$@" && NAMESPACE="$2" && shift 2 ;;
    -h | --help)
        usage
        exit 0
        ;;
    *)
        echo "! unknown argument: $1" >&2
        usage
        exit 2
        ;;
    esac
done

[[ -n "${MODE}" ]] || { usage && exit 2; }

# Captured once, then matched with a here-string: piping kubectl into `grep -q` makes grep
# exit on the first match, kubectl take SIGPIPE, and pipefail read the whole pipeline as a
# failure — so a context that exists is intermittently reported as missing.
CONTEXTS=$(kubectl config get-contexts -o name)
if [[ -z "${CONTEXT}" ]]; then
    echo "! --context is required (available: $(tr '\n' ' ' <<<"${CONTEXTS}"))" >&2
    exit 2
fi

if ! grep -qx -- "${CONTEXT}" <<<"${CONTEXTS}"; then
    echo "! no such kube context: ${CONTEXT}" >&2
    echo "  available: $(tr '\n' ' ' <<<"${CONTEXTS}")" >&2
    exit 2
fi

k() { kubectl --context "${CONTEXT}" -n "${NAMESPACE}" "$@"; }

# BSD base64 wants -D, GNU wants -d.
if base64 -d </dev/null >/dev/null 2>&1; then b64d() { base64 -d; }; else b64d() { base64 -D; }; fi
b64e() { base64 | tr -d '\n'; }

# Flip openid.<provider>.autoLogin in the mounted provider properties. XNAT reads the
# file at startup, so callers must restart the pods afterwards.
#
# The role names this Secret xnat-plugin-<authplugins entry> with key
# <provider>-provider-properties (roles/xnat/tasks/create_secrets.yaml), both of which
# come from inventory — so derive them from the mounted volume rather than guessing.
set_autologin() {
    local want="$1" secret key current decoded updated

    read -r secret key < <(k get statefulset "${RELEASE}" -o \
        'jsonpath={range .spec.template.spec.volumes[?(@.secret)]}{.secret.secretName}{" "}{.secret.items[0].key}{"\n"}{end}' |
        grep -m1 -- '-provider-properties' || true)

    if [[ -z "${secret:-}" ]]; then
        echo "  ! no auth-provider properties Secret mounted — skipping auto-login change"
        return 0
    fi

    if ! current=$(k get secret "${secret}" -o "jsonpath={.data.${key}}" 2>/dev/null) || [[ -z "${current}" ]]; then
        echo "  ! secret ${secret} has no ${key} — skipping auto-login change"
        return 0
    fi

    decoded=$(printf '%s' "${current}" | b64d)
    if ! grep -q '^openid\.[^.]*\.autoLogin=' <<<"${decoded}"; then
        echo "  ! no autoLogin property in ${secret} — skipping"
        return 0
    fi

    updated=$(sed -E "s/^(openid\.[^.]*\.autoLogin)=.*/\1=${want}/" <<<"${decoded}" | b64e)
    k patch secret "${secret}" --type merge -p "{\"data\":{\"${key}\":\"${updated}\"}}" >/dev/null
    echo "  autoLogin=${want} (${secret})"
}

port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1; }

# XNAT builds redirect targets from its configured siteUrl. Left pointing at the public
# hostname, any client that follows a redirect (RestAssured does by default) leaves the
# port-forward, hits the ingress, and gets oauth2-proxy's 401 — which reads as an auth
# failure even though the operation succeeded. Point it at the forward for the duration.
#
# Talks to XNAT through `kubectl exec` rather than the forward, so it works in `off` too,
# after the forwards are gone. The admin credential comes from the Secret the role seeds.
xnat_api() {
    local pw
    pw=$(k get secret "${RELEASE}-prefs-init" -o 'jsonpath={.data.prefs-init\.ini}' 2>/dev/null |
        b64d | sed -n 's/^defaultAdminPassword=//p')
    [[ -n "${pw}" ]] || return 1
    k exec "${RELEASE}-0" -c xnat -- curl -s -u "admin:${pw}" "$@" 2>/dev/null
}

set_siteurl() {
    local want="$1"
    xnat_api -o /dev/null -X POST -H 'Content-Type: application/json' \
        -d "{\"siteUrl\":\"${want}\"}" http://localhost:8080/xapi/siteConfig ||
        { echo "  ! could not set siteUrl (is the admin credential still the seeded one?)"; return 0; }
    echo "  siteUrl=${want}"
}

stop_forwards() {
    if [[ -f "${PID_FILE}" ]]; then
        while read -r pid; do kill "${pid}" 2>/dev/null || true; done <"${PID_FILE}"
        rm -f "${PID_FILE}"
    fi
    pkill -f "port-forward pod/${RELEASE}-0" 2>/dev/null || true
}

start_forwards() {
    local port
    : >"${PID_FILE}"
    for port in "${REST_PORT}" "${DICOM_PORT}"; do
        if port_in_use "${port}"; then
            echo "  ! port ${port} is already in use — free it and re-run" >&2
            exit 1
        fi
        kubectl --context "${CONTEXT}" -n "${NAMESPACE}" \
            port-forward "pod/${RELEASE}-0" "${port}:${port}" >/dev/null 2>&1 &
        echo $! >>"${PID_FILE}"
    done
    echo "  port-forwards: ${REST_PORT} (REST), ${DICOM_PORT} (DICOM) -> ${RELEASE}-0"
}

# Readiness probes are TCP-only and pass as soon as Tomcat binds, roughly two minutes
# before the WAR finishes deploying — so poll the endpoint the suite actually probes.
wait_for_xnat() {
    local code
    for _ in $(seq 1 60); do
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
            "http://localhost:${REST_PORT}/app/template/Login.vm" 2>/dev/null || true)
        if [[ "${code}" == "200" ]]; then
            echo "  XNAT serving: /app/template/Login.vm -> 200"
            return 0
        fi
        sleep 5
    done
    echo "  ! XNAT did not return 200 within 5 minutes (last: ${code:-no response})" >&2
    echo "    check: kubectl --context ${CONTEXT} -n ${NAMESPACE} logs ${RELEASE}-0 -c xnat" >&2
    exit 1
}

case "${MODE}" in
on)
    echo "XNAT test mode ON  (context=${CONTEXT} namespace=${NAMESPACE})"

    current_replicas=$(k get statefulset "${RELEASE}" -o 'jsonpath={.spec.replicas}')
    if [[ -z "$(k get statefulset "${RELEASE}" -o "jsonpath={.metadata.annotations.${REPLICAS_ANNOTATION//./\\.}}")" ]]; then
        k annotate statefulset "${RELEASE}" "${REPLICAS_ANNOTATION}=${current_replicas}" --overwrite >/dev/null
    fi
    k scale statefulset "${RELEASE}" --replicas=1 >/dev/null
    echo "  replicas: ${current_replicas} -> 1"

    set_autologin false

    k rollout restart statefulset "${RELEASE}" >/dev/null
    k rollout status statefulset "${RELEASE}" --timeout=5m >/dev/null
    echo "  ${RELEASE}-0 restarted"

    stop_forwards
    start_forwards
    wait_for_xnat

    saved_siteurl=$(xnat_api http://localhost:8080/xapi/siteConfig/siteUrl || true)
    if [[ -n "${saved_siteurl}" && "${saved_siteurl}" != "http://localhost:${REST_PORT}" ]]; then
        k annotate statefulset "${RELEASE}" "${SITEURL_ANNOTATION}=${saved_siteurl}" --overwrite >/dev/null
    fi
    set_siteurl "http://localhost:${REST_PORT}"

    cat <<EOF

Ready (1 replica, auto-login off, siteUrl pointed at the forward so redirects stay local).
In src/test/resources/config/local.properties:
  xnat.baseurl=http://localhost:${REST_PORT}
  xnat.dicom.host=localhost
  xnat.dicom.port=${DICOM_PORT}
  xnat.dicom.aetitle=XNAT

Run '$(basename "$0") off' when you are done.
EOF
    ;;
off)
    echo "XNAT test mode OFF (context=${CONTEXT} namespace=${NAMESPACE})"

    stop_forwards
    echo "  port-forwards stopped"

    set_autologin true

    saved_siteurl=$(k get statefulset "${RELEASE}" -o "jsonpath={.metadata.annotations.${SITEURL_ANNOTATION//./\\.}}")
    if [[ -n "${saved_siteurl}" ]]; then
        set_siteurl "${saved_siteurl}"
        k annotate statefulset "${RELEASE}" "${SITEURL_ANNOTATION}-" >/dev/null
    fi

    saved=$(k get statefulset "${RELEASE}" -o "jsonpath={.metadata.annotations.${REPLICAS_ANNOTATION//./\\.}}")
    if [[ -n "${saved}" ]]; then
        k scale statefulset "${RELEASE}" --replicas="${saved}" >/dev/null
        k annotate statefulset "${RELEASE}" "${REPLICAS_ANNOTATION}-" >/dev/null
        echo "  replicas: 1 -> ${saved}"
    else
        echo "  ! no saved replica count — leaving replicas as-is"
    fi

    k rollout restart statefulset "${RELEASE}" >/dev/null
    echo "  rolling restart started (pods pick up auto-login on restart)"
    ;;
*)
    usage
    exit 2
    ;;
esac
