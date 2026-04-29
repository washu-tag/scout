#!/usr/bin/env bash
#
# End-to-end driver for the xnat-k3d test runbook. Runs every step in
# docs/internal/xnat-k3d-runbook.md and asserts health between phases so
# the script exits non-zero with a useful section marker on first failure
# instead of marching forward into a broken state.
#
# Idempotent: create-cluster.sh wipes any prior cluster, secrets/namespace
# use server-side apply, and the kcadm calls check before creating.
#
# Prereqs: see runbook §0. In particular, run `mkcert -install` once before
# the first invocation so agent-browser trusts *.localtest.me. If you're
# using the openssl fallback (no mkcert), pass AB_INSECURE=1 to skip cert
# verification in the agent-browser test phase.
#
# Usage:
#   ./xnat/k3d/run-all.sh
#   AB_INSECURE=1 ./xnat/k3d/run-all.sh        # skip TLS verify in tests
#   HELM_CHART=/path/to/helm/xnat ./xnat/k3d/run-all.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ANSIBLE_DIR="${REPO_ROOT}/ansible"
XNAT_DIR="${REPO_ROOT}/xnat"
K3D_DIR="${XNAT_DIR}/k3d"
HELM_CHART="${HELM_CHART:-/Users/jflavin/repos/helm-charts/helm/xnat}"
XNAT_CLIENT_SECRET="${XNAT_CLIENT_SECRET:-a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6}"

step() { printf '\n=== %s ===\n' "$*"; }
fail() { printf '\n!!! FAIL at: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
step "1. Create k3d cluster + TLS material"
cd "${K3D_DIR}"
./create-cluster.sh
export KUBECONFIG="${K3D_DIR}/kubeconfig"
kubectl get nodes >/dev/null || fail "step 1: cluster not reachable"

# ---------------------------------------------------------------------------
step "2. Apply Traefik TLS config"
cd "${ANSIBLE_DIR}"
ansible-playbook -i inventory.k3d.yaml playbooks/dev-tls.yaml

echo "Waiting for traefik rollout..."
kubectl -n kube-system rollout status deployment/traefik --timeout=2m \
  || fail "step 2: traefik did not roll out"

echo "Probing ingress is listening..."
until curl -sk --max-time 3 -o /dev/null https://keycloak.localtest.me; do
  sleep 2
done

# ---------------------------------------------------------------------------
step "3. Install Postgres + Keycloak DB"
ansible-playbook -i inventory.k3d.yaml playbooks/postgres.yaml

echo "Waiting for CNPG cluster to become healthy..."
kubectl wait --for=jsonpath='{.status.phase}'='Cluster in healthy state' \
  cluster/postgresql-cluster -n scout-core --timeout=5m \
  || fail "step 3: postgres cluster not healthy"

# ---------------------------------------------------------------------------
step "4. Install Keycloak"
ansible-playbook -i inventory.k3d.yaml playbooks/auth-keycloak-only.yaml

echo "Waiting for Keycloak pod to appear and become Ready..."
until kubectl -n scout-core get pod -l app=keycloak 2>/dev/null \
        | grep -q keycloak; do
  sleep 2
done
kubectl -n scout-core wait --for=condition=Ready pod -l app=keycloak \
  --timeout=5m \
  || fail "step 4: keycloak pod not Ready"

echo "Waiting for keycloak-config-cli realm-import Job..."
KCC_JOB=$(kubectl -n scout-core get job -o name 2>/dev/null \
            | grep -i config-cli | head -1 || true)
if [[ -n "${KCC_JOB}" ]]; then
  kubectl -n scout-core wait --for=condition=Complete "${KCC_JOB}" \
    --timeout=5m \
    || fail "step 4: keycloak-config-cli did not complete"
fi

echo "Probing realm well-known endpoint..."
until curl -skf -o /dev/null \
        https://keycloak.localtest.me/realms/scout/.well-known/openid-configuration; do
  sleep 2
done

# Make keycloak.localtest.me resolvable from inside the cluster.
# Outside the cluster, keycloak.localtest.me → 127.0.0.1 (public DNS), which
# from the host's POV hits the k3d load balancer on :443. From inside a pod
# 127.0.0.1 is the pod's own loopback — token-exchange and userinfo calls
# from XNAT to Keycloak get "Connection refused" and the OIDC callback
# silently fails. Fix: install a CoreDNS *.server override that points
# keycloak.localtest.me at the Traefik cluster IP, so in-cluster pods reach
# Traefik (which then routes via the Keycloak Ingress).
echo "Patching CoreDNS so keycloak.localtest.me resolves in-cluster..."
TRAEFIK_IP=$(kubectl -n kube-system get svc traefik \
               -o jsonpath='{.spec.clusterIP}')
[[ -n "${TRAEFIK_IP}" ]] || fail "step 4: could not read traefik clusterIP"
kubectl -n kube-system create configmap coredns-custom \
  --from-literal="keycloak.localtest.me.server=keycloak.localtest.me:53 {
    template IN A {
        answer \"{{ .Name }} 60 IN A ${TRAEFIK_IP}\"
    }
}" \
  --dry-run=client -o yaml | kubectl apply -f -
# Bounce CoreDNS so it picks up the override immediately (the import only
# rescans on pod start; the Corefile itself is unchanged).
kubectl -n kube-system rollout restart deployment/coredns
kubectl -n kube-system rollout status deployment/coredns --timeout=2m \
  || fail "step 4: coredns did not roll out after configmap update"

# ---------------------------------------------------------------------------
step "5. Configure Keycloak: scout-user group, alice/bob, groups mapper"
KC_POD=$(kubectl -n scout-core get pod -l app=keycloak \
           -o jsonpath='{.items[0].metadata.name}')
KC="kubectl -n scout-core exec -i ${KC_POD} -- /opt/keycloak/bin/kcadm.sh"

${KC} config credentials --server http://localhost:8080 \
  --realm master --user admin --password admin

if ! ${KC} get groups -r scout -q search=scout-user \
       --fields name --format csv --noquotes 2>/dev/null \
       | grep -qx scout-user; then
  ${KC} create groups -r scout -s name=scout-user
fi

ensure_user() {
  local user="$1" email="$2" first="$3" last="$4"
  if ! ${KC} get users -r scout -q "username=${user}" \
         --fields username --format csv --noquotes 2>/dev/null \
         | grep -qx "${user}"; then
    ${KC} create users -r scout \
      -s "username=${user}" -s enabled=true \
      -s "email=${email}" -s emailVerified=true \
      -s "firstName=${first}" -s "lastName=${last}"
  fi
}
ensure_user alice alice@localtest.me Alice Test
ensure_user bob   bob@localtest.me   Bob   Test

${KC} set-password -r scout --username alice --new-password alice-pw
${KC} set-password -r scout --username bob   --new-password bob-pw

ALICE_ID=$(${KC} get users -r scout -q username=alice \
             --fields id --format csv --noquotes | tail -1)
GROUP_ID=$(${KC} get groups -r scout -q search=scout-user \
             --fields id --format csv --noquotes | tail -1)
${KC} update "users/${ALICE_ID}/groups/${GROUP_ID}" -r scout -n

XNAT_CID=$(${KC} get clients -r scout -q clientId=xnat \
             --fields id --format csv --noquotes | tail -1)
if ! ${KC} get "clients/${XNAT_CID}/protocol-mappers/models" -r scout \
       --fields name --format csv --noquotes 2>/dev/null \
       | grep -qx groups; then
  ${KC} create "clients/${XNAT_CID}/protocol-mappers/models" -r scout -f - <<'JSON'
{
  "name": "groups",
  "protocol": "openid-connect",
  "protocolMapper": "oidc-group-membership-mapper",
  "config": {
    "claim.name": "groups",
    "full.path": "false",
    "id.token.claim": "true",
    "access.token.claim": "true",
    "userinfo.token.claim": "true"
  }
}
JSON
fi

# Sanity-check: alice's id_token should contain groups: ["scout-user"].
# The production xnat client only uses the OIDC redirect flow, so the realm
# template doesn't enable directAccessGrantsEnabled. Flip it on here for the
# k3d cluster so we can do a password-grant smoke test before driving the
# real login through agent-browser in step 11.
${KC} update "clients/${XNAT_CID}" -r scout -s directAccessGrantsEnabled=true

echo "Verifying alice's token contains scout-user claim..."
ALICE_RESP=$(curl -sk \
  -d grant_type=password -d client_id=xnat \
  -d "client_secret=${XNAT_CLIENT_SECRET}" \
  -d username=alice -d password=alice-pw -d scope=openid \
  https://keycloak.localtest.me/realms/scout/protocol/openid-connect/token)
ALICE_TOKEN=$(echo "${ALICE_RESP}" | python3 -c 'import sys,json; d=json.load(sys.stdin); sys.exit(f"token error: {d}") if "id_token" not in d else print(d["id_token"])') \
  || fail "step 5: keycloak refused alice password grant: ${ALICE_RESP}"
ALICE_GROUPS=$(echo "${ALICE_TOKEN}" | cut -d. -f2 \
  | python3 -c 'import sys,json,base64; p=sys.stdin.read().strip(); print(json.loads(base64.urlsafe_b64decode(p+"==")).get("groups",[]))')
echo "  alice groups claim: ${ALICE_GROUPS}"
echo "${ALICE_GROUPS}" | grep -q scout-user \
  || fail "step 5: alice's token is missing the scout-user groups claim"

# Plan B for the alice/bob gate: 1.4.1-xpl has no group-filtering support,
# so we let Keycloak refuse non-scout-user logins to the xnat client. Wire
# up a `xnat-access` client role mapped from scout-user, then override the
# xnat client's browser flow with a conditional sub-flow that runs Deny
# Access when the user is missing the role. Idempotent.

echo "Ensuring xnat-access client role exists..."
if ! ${KC} get "clients/${XNAT_CID}/roles" -r scout \
       --fields name --format csv --noquotes 2>/dev/null \
       | grep -qx xnat-access; then
  ${KC} create "clients/${XNAT_CID}/roles" -r scout \
    -s name=xnat-access \
    -s 'description=Required for XNAT login (gated by browser-xnat-access flow override)'
fi

echo "Mapping xnat-access to scout-user group..."
# add-roles is idempotent — adding an existing mapping is a no-op.
${KC} add-roles -r scout --gname scout-user --cclientid xnat \
  --rolename xnat-access >/dev/null

echo "Building browser-xnat-access authentication flow..."
if ! ${KC} get authentication/flows -r scout \
       --fields alias --format csv --noquotes 2>/dev/null \
       | grep -qx browser-xnat-access; then
  ${KC} create authentication/flows/browser/copy -r scout \
    -s newName=browser-xnat-access >/dev/null
fi

# Sub-flow names contain spaces; URL-encode them when used in REST paths.
FORMS_FLOW='browser-xnat-access%20forms'
GATE_FLOW='browser-xnat-access%20require-xnat-access'

# Create the gate sub-flow inside `forms` if missing.
if ! ${KC} get "authentication/flows/${FORMS_FLOW}/executions" -r scout 2>/dev/null \
       | python3 -c "import sys,json; sys.exit(0 if any(e.get('displayName','').endswith('require-xnat-access') for e in json.load(sys.stdin)) else 1)"; then
  ${KC} create "authentication/flows/${FORMS_FLOW}/executions/flow" -r scout \
    -b '{"alias": "browser-xnat-access require-xnat-access", "type": "basic-flow", "description": "Require xnat-access client role", "provider": "registration-page-form"}' \
    >/dev/null
fi

# Get IDs for the gate sub-flow execution + its child executions.
GATE_SUBFLOW_EXEC=$(${KC} get "authentication/flows/${FORMS_FLOW}/executions" -r scout \
  | python3 -c "import sys,json; print(next(e['id'] for e in json.load(sys.stdin) if e.get('displayName','').endswith('require-xnat-access') and e.get('authenticationFlow')))")

# Add Condition - User Role + Deny Access executions inside the gate sub-flow.
GATE_EXECS=$(${KC} get "authentication/flows/${GATE_FLOW}/executions" -r scout)
if ! echo "${GATE_EXECS}" | grep -q '"conditional-user-role"'; then
  ${KC} create "authentication/flows/${GATE_FLOW}/executions/execution" -r scout \
    -b '{"provider": "conditional-user-role"}' >/dev/null
fi
if ! echo "${GATE_EXECS}" | grep -q '"deny-access-authenticator"'; then
  ${KC} create "authentication/flows/${GATE_FLOW}/executions/execution" -r scout \
    -b '{"provider": "deny-access-authenticator"}' >/dev/null
fi

# Set requirements: gate sub-flow CONDITIONAL, child executions REQUIRED.
${KC} update "authentication/flows/${FORMS_FLOW}/executions" -r scout \
  -b "{\"id\": \"${GATE_SUBFLOW_EXEC}\", \"requirement\": \"CONDITIONAL\"}" \
  >/dev/null
COND_EXEC_ID=$(${KC} get "authentication/flows/${GATE_FLOW}/executions" -r scout \
  | python3 -c "import sys,json; print(next(e['id'] for e in json.load(sys.stdin) if e.get('providerId')=='conditional-user-role'))")
DENY_EXEC_ID=$(${KC} get "authentication/flows/${GATE_FLOW}/executions" -r scout \
  | python3 -c "import sys,json; print(next(e['id'] for e in json.load(sys.stdin) if e.get('providerId')=='deny-access-authenticator'))")
${KC} update "authentication/flows/${GATE_FLOW}/executions" -r scout \
  -b "{\"id\": \"${COND_EXEC_ID}\", \"requirement\": \"REQUIRED\"}" >/dev/null
${KC} update "authentication/flows/${GATE_FLOW}/executions" -r scout \
  -b "{\"id\": \"${DENY_EXEC_ID}\", \"requirement\": \"REQUIRED\"}" >/dev/null

# Configure the Condition - User Role: client role xnat.xnat-access,
# negate so the rule fires when the user is *missing* the role.
COND_HAS_CONFIG=$(${KC} get "authentication/flows/${GATE_FLOW}/executions" -r scout \
  | python3 -c "import sys,json; print('yes' if next(e for e in json.load(sys.stdin) if e.get('providerId')=='conditional-user-role').get('authenticationConfig') else 'no')")
if [[ "${COND_HAS_CONFIG}" == "no" ]]; then
  ${KC} create "authentication/executions/${COND_EXEC_ID}/config" -r scout \
    -b '{"alias": "browser-xnat-access cond config", "config": {"condUserRole": "xnat.xnat-access", "negate": "true"}}' \
    >/dev/null
fi

# Bind browser-xnat-access flow as the xnat client's browser flow override.
BX_FLOW_ID=$(${KC} get authentication/flows -r scout \
  | python3 -c "import sys,json; print(next(f['id'] for f in json.load(sys.stdin) if f['alias']=='browser-xnat-access'))")
${KC} update "clients/${XNAT_CID}" -r scout \
  -s "authenticationFlowBindingOverrides.browser=${BX_FLOW_ID}" \
  >/dev/null

# ---------------------------------------------------------------------------
step "6. Prepare XNAT manifests (sed + prefs-init + values overrides)"
cd "${XNAT_DIR}"
mkdir -p k3d/manifests

sed 's/dev03\.tag\.rcif\.io/localtest.me/g' xnat-values.yaml \
  > k3d/manifests/xnat-values.yaml
sed 's/dev03\.tag\.rcif\.io/localtest.me/g' openid-provider.properties \
  > k3d/manifests/openid-provider.properties

sed -i.bak 's/^auto.enabled=false/auto.enabled=true/' \
  k3d/manifests/openid-provider.properties
rm k3d/manifests/openid-provider.properties.bak

if ! grep -q '^openid.keycloak.allowedGroups=' \
       k3d/manifests/openid-provider.properties; then
  cat >> k3d/manifests/openid-provider.properties <<'EOF'

# Auto-enable Keycloak-provisioned users on first login. With the group
# filter below: alice (in scout-user) lands logged in; bob (not in
# scout-user) is rejected at the OIDC callback. Verify property name
# against openid-auth-plugin 1.4.1-xpl README — see runbook §6 for fixes
# if 1.4.1-xpl doesn't honor allowedGroups.
openid.keycloak.allowedGroups=scout-user
EOF
fi

cat > k3d/manifests/prefs-init.ini <<'EOF'
[siteConfig]
siteUrl=https://xnat.localtest.me
siteId=scout-xnat-k3d
adminEmail=admin@localtest.me
smtpEnabled=false
initialized=true
requireLogin=true
EOF

# Append the JVM-trust + prefs-init values block. Note the inner heredoc
# uses INNER_EOF so the outer VALUES_EOF terminator isn't hit prematurely
# — this nesting matters because the chart consumes the YAML which itself
# embeds a bash script that uses a heredoc.
if ! grep -q '^extraVolumes:' k3d/manifests/xnat-values.yaml; then
  cat >> k3d/manifests/xnat-values.yaml <<'VALUES_EOF'

extraVolumes:
  - name: local-ca
    secret:
      secretName: local-ca
      items:
        - key: rootCA.pem
          path: rootCA.pem
  - name: jvm-trust
    emptyDir: {}
  - name: prefs-init
    secret:
      secretName: xnat-prefs-init

extraVolumeMounts:
  - name: jvm-trust
    mountPath: /etc/scout-trust
  - name: prefs-init
    mountPath: /data/xnat/home/config/prefs-init.ini
    subPath: prefs-init.ini

initContainers:
  - name: install-openid-plugin
    image: curlimages/curl:8.10.1
    command: ['sh', '-c']
    args:
      - >
        set -e;
        curl -fsSL -o /data/xnat/home/plugins/openid-auth-plugin.jar
        https://bitbucket.org/xnatx/openid-auth-plugin/downloads/openid-auth-plugin-1.4.1-xpl.jar
    volumeMounts:
      - name: home-plugins
        mountPath: /data/xnat/home/plugins
  - name: build-truststore
    image: xnatworks/xnat-web:1.9.2.1
    imagePullPolicy: IfNotPresent
    command: ['bash', '-c']
    args:
      - |
        set -euo pipefail
        SRC="${JAVA_HOME}/lib/security/cacerts"
        [ -f "$SRC" ] || SRC="${JAVA_HOME}/jre/lib/security/cacerts"
        cp "$SRC" /trust/cacerts
        chmod 644 /trust/cacerts
        keytool -importcert -trustcacerts -noprompt \
          -alias mkcert-local-ca \
          -file /ca/rootCA.pem \
          -keystore /trust/cacerts \
          -storepass changeit
        mkdir -p /tomcat/bin
        cat > /tomcat/bin/setenv.sh <<'INNER_EOF'
        export JAVA_OPTS="-Djavax.net.ssl.trustStore=/etc/scout-trust/cacerts -Djavax.net.ssl.trustStorePassword=changeit ${JAVA_OPTS:-}"
        INNER_EOF
        chmod +x /tomcat/bin/setenv.sh
    volumeMounts:
      - name: local-ca
        mountPath: /ca
        readOnly: true
      - name: jvm-trust
        mountPath: /trust
      - name: tomcat
        mountPath: /tomcat
VALUES_EOF
fi

# ---------------------------------------------------------------------------
step "7. helm dependency update"
( cd "${HELM_CHART}" && helm dependency update )

# ---------------------------------------------------------------------------
step "8. Create namespace and secrets"
cd "${REPO_ROOT}"
kubectl create namespace xnat --dry-run=client -o yaml | kubectl apply -f -

kubectl -n xnat create secret generic xnat-plugin-keycloak \
  --from-file=keycloak-provider-properties=xnat/k3d/manifests/openid-provider.properties \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n xnat create secret generic xnat-prefs-init \
  --from-file=prefs-init.ini=xnat/k3d/manifests/prefs-init.ini \
  --dry-run=client -o yaml | kubectl apply -f -

# Prefer mkcert's rootCA.pem; fall back to the openssl self-signed leaf.
ROOT_CA_SRC="${K3D_DIR}/certs/rootCA.pem"
[[ -f "${ROOT_CA_SRC}" ]] || ROOT_CA_SRC="${K3D_DIR}/certs/localtest.me.crt"
kubectl -n xnat create secret generic local-ca \
  --from-file=rootCA.pem="${ROOT_CA_SRC}" \
  --dry-run=client -o yaml | kubectl apply -f -

# The xnat helm chart pulls in the bokysan/postfix subchart unconditionally
# (no mail.enabled flag), and that subchart requires a secret named
# postfix-password to exist. We don't actually use mail (prefs-init.ini sets
# smtpEnabled=false), but without this secret the mail pod sits in
# CreateContainerConfigError and helm install --wait never returns.
kubectl -n xnat create secret generic postfix-password \
  --from-literal=username=xnat --from-literal=password=unused-dev \
  --dry-run=client -o yaml | kubectl apply -f -

# ---------------------------------------------------------------------------
step "10. Helm install xnat (5-15 min on first boot due to Hibernate ddl)"
helm upgrade --install xnat "${HELM_CHART}" \
  --kubeconfig "${KUBECONFIG}" \
  --namespace xnat \
  --values xnat/k3d/manifests/xnat-values.yaml \
  --set probes.startup.failureThreshold=60 \
  --wait --timeout 20m \
  || fail "step 10: helm install timed out or failed; check kubectl -n xnat get pods"

echo "Confirming openid-auth-plugin loaded..."
# The XNAT logback config only routes ERROR-level logs to stdout, so the
# plugin scan is invisible there. Query the /xapi/plugins API instead — the
# plugin shows up under id "openIdAuthPlugin" once it's wired in.
curl -sku admin:admin https://xnat.localtest.me/xapi/plugins \
  | grep -q '"openIdAuthPlugin"' \
  || fail "step 10: openIdAuthPlugin not registered in /xapi/plugins"

# XNAT site config defaults to enabledProviders=["localdb"] — the keycloak
# provider has to be explicitly enabled or it won't be eligible at the
# /openid-login route, regardless of whether the plugin loaded. Idempotent.
echo "Enabling keycloak as a site auth provider..."
curl -sku admin:admin -X POST -H 'Content-Type: application/json' \
  -d '{"enabledProviders": ["localdb", "keycloak"]}' \
  https://xnat.localtest.me/xapi/siteConfig >/dev/null \
  || fail "step 10: failed to update enabledProviders via /xapi/siteConfig"

# Verify the keycloak provider responds at the route: a GET on
# /openid-login?providerId=keycloak should 302 to the Keycloak auth endpoint.
KC_REDIR=$(curl -sk -o /dev/null -w '%{redirect_url}' \
  "https://xnat.localtest.me/openid-login?providerId=keycloak")
[[ "${KC_REDIR}" == https://keycloak.localtest.me/* ]] \
  || fail "step 10: /openid-login did not redirect to Keycloak (got: ${KC_REDIR})"

echo "Confirming setup wizard was bypassed..."
if curl -sk https://xnat.localtest.me/ | grep -iE 'setup|initialize' >/dev/null; then
  fail "step 10: XNAT still showing setup wizard — prefs-init.ini not effective"
fi

# ---------------------------------------------------------------------------
step "11. Run agent-browser login assertions"
"${K3D_DIR}/test-login.sh"

# ---------------------------------------------------------------------------
step "DONE"
echo "All phases passed. Cluster: scout-xnat-dev"
echo "  KUBECONFIG=${KUBECONFIG}"
echo "  Tear down with: ${K3D_DIR}/teardown-cluster.sh"
