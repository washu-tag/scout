# XNAT on Local k3d — Test Runbook

A laptop-friendly variant of `xnat-deployment-runbook.md`, used when the dev
cluster isn't available. Goal: exercise the XNAT ↔ Keycloak login flow end to
end against a single-node k3d cluster running on `localhost`, and assert that
**only members of the `scout-user` Keycloak group can sign in to XNAT**.
Everything else in Scout (lake, analytics, monitoring, oauth2-proxy, etc.)
is deliberately skipped — see `xnat-deployment-plan.md` for what's needed
and why.

The flow is end-to-end automated: keycloak-config-cli imports the realm
(xnat client, xnat-access role + scout-user mapping, group membership
mapper, browser-xnat-access flow override), kcadm seeds the test fixtures
(scout-user group, alice/bob users), an XNAT `prefs-init.ini` skips the
first-boot setup wizard, and an `agent-browser` script drives the OIDC
login as both an authorized user (`alice`, in `scout-user`) and an
unauthorized user (`bob`, no group) with explicit pass/fail assertions.

> **TL;DR — just run it:** `xnat/k3d/run-all.sh` drives every step below
> end-to-end with health waits between phases and exits non-zero on the
> first failure with a section marker. Use `AB_INSECURE=1
> xnat/k3d/run-all.sh` if you skipped `mkcert -install`. The numbered
> sections in this document exist so you can run each phase manually when
> debugging — they're the ground truth the script is automating, not a
> separate workflow.

All `ansible-playbook` commands assume CWD is the `ansible/` directory. The
`playbooks/main.yaml` `Makefile` shortcuts are not used here; commands are
written explicitly so they're easy to re-run individually.

## 0. Prereqs

The boundary: anything that touches your laptop's installed software or
system trust state is **manual** (this section). Anything that touches the
k3d cluster is **automated** by `run-all.sh` (§1–§11).

### Manual one-time setup (do these once before the first run)

- **Tools on PATH:** `k3d`, `helm`, `kubectl`, `ansible`, `agent-browser`,
  `python3`, `curl`. `mkcert` is strongly recommended; the cluster script
  falls back to `openssl` self-signed if it's not present.
- **Run `mkcert -install` once.** This adds mkcert's root CA to the macOS
  keychain, which is what the bundled Chromium inside `agent-browser`
  reads when validating `*.localtest.me` certs. **The wrapper script does
  not do this for you** — it's host-level trust state with security
  implications. Without it, you must pass `AB_INSECURE=1` to
  `run-all.sh` (or `xnat/k3d/test-login.sh`) to skip TLS verification in
  the login tests, and you lose the host-side check that the trust chain
  is correct.
- **Ports 80 and 443 free on the host** — k3d's load balancer binds them.
  Cluster creation fails if they're occupied; the script doesn't try to
  free them.
- **XNAT Helm chart cloned locally** at
  `/Users/jflavin/repos/helm-charts/helm/xnat` (or override with
  `HELM_CHART=/path/to/helm/xnat ./xnat/k3d/run-all.sh`). The chart isn't
  cloned automatically because it's an external repo with its own branch
  and version concerns.

### Already in this branch (no action needed)

- `ansible/inventory.k3d.yaml`, `ansible/playbooks/dev-tls.yaml`,
  `ansible/playbooks/auth-keycloak-only.yaml`, the `xnat/k3d/` tree.
- Vault password file at `ansible/vault/pwd.sh` is **not** required for
  this inventory — `inventory.k3d.yaml` ships plaintext dev secrets.

### Automated by `run-all.sh` (§1–§11)

Cluster creation, TLS config, Postgres + Keycloak install, kcadm
seeding of test fixtures (scout-user group, alice/bob users), XNAT
manifest generation, namespace/secret creation, Helm install, and the
agent-browser login assertions — all with health waits between phases
and a non-zero exit on the first failure.

## 1. Create the k3d cluster + TLS material

```bash
cd xnat/k3d
./create-cluster.sh
export KUBECONFIG="$PWD/kubeconfig"
kubectl get nodes
```

The script:
- Deletes any prior `scout-xnat-dev` cluster, then creates a fresh one with
  `:80` and `:443` mapped to the load balancer
- Generates `certs/localtest.me.{crt,key}` with SANs for `localtest.me` and
  `*.localtest.me` (mkcert if available, else openssl)
- Writes a kubeconfig to `xnat/k3d/kubeconfig`

> No `/etc/hosts` edits needed: `*.localtest.me` resolves to `127.0.0.1` via
> public DNS. `xnat.localtest.me` and `keycloak.localtest.me` will route to
> the k3d ingress.

## 2. Apply Traefik TLS config

```bash
cd ../../ansible
ansible-playbook -i inventory.k3d.yaml playbooks/dev-tls.yaml
```

This loads the cert into a `kubernetes.io/tls` Secret in `kube-system`, sets
the default `TLSStore`, and applies a `HelmChartConfig` that turns on the
`websecure` entrypoint and redirects `:80 → :443`. Wait ~30s for k3s to roll
the helm-install job, then:

```bash
kubectl -n kube-system get pods -l app.kubernetes.io/name=traefik
curl -kI https://keycloak.localtest.me   # expect 404 from Traefik (no backend yet)
```

## 3. Install Postgres (CNPG) + Keycloak DB

```bash
ansible-playbook -i inventory.k3d.yaml playbooks/postgres.yaml
```

Watch for the cluster to come up:

```bash
kubectl -n scout-core get cluster postgresql-cluster
kubectl -n scout-core get pods
```

The CNPG operator lives in `scout-operators`; the cluster lives in
`scout-core`. The XNAT Helm chart will provision its **own** CNPG cluster in
the `xnat` namespace later — Scout's central cluster is only used by Keycloak.

## 4. Install Keycloak (no oauth2-proxy)

```bash
ansible-playbook -i inventory.k3d.yaml playbooks/auth-keycloak-only.yaml
```

This applies the Keycloak operator + CRDs, creates the Keycloak CR, runs
keycloak-config-cli to import `scout-realm.json` (which now includes the
`xnat` client thanks to this branch), and creates the Keycloak Ingress.

Verify the realm is up:

```bash
kubectl -n scout-core get keycloak keycloak
kubectl -n scout-core get ingress keycloak-ingress
curl -kI https://keycloak.localtest.me
```

(No manual UI verification needed — step 5 reads the same state via kcadm
and will fail loudly if anything is missing.)

## 5. Configure Keycloak test fixtures (scout-user group, alice/bob users)

This step replaces the manual "browse to Keycloak admin, click around,
create a test user" flow with `kcadm.sh` calls run inside the Keycloak
pod. The realm template (imported in step 4 via keycloak-config-cli)
already ships everything that's *durable* — the `xnat` client, the
`xnat-access` client role, the scout-user → xnat-access mapping, the
Group Membership protocol mapper that emits the `groups` claim, and the
`browser-xnat-access` flow override. What's left for this step is the
test-only fixture: the `scout-user` group itself and the alice/bob users.

```bash
KC_POD=$(kubectl -n scout-core get pod -l app=keycloak -o jsonpath='{.items[0].metadata.name}')
KC="kubectl -n scout-core exec -i ${KC_POD} -- /opt/keycloak/bin/kcadm.sh"

# Authenticate kcadm against the local admin realm
${KC} config credentials \
  --server http://localhost:8080 \
  --realm master \
  --user admin --password admin

# Group + users
${KC} create groups -r scout -s name=scout-user
${KC} create users -r scout -s username=alice -s enabled=true \
  -s email=alice@localtest.me -s emailVerified=true \
  -s firstName=Alice -s lastName=Test
${KC} set-password -r scout --username alice --new-password alice-pw
${KC} create users -r scout -s username=bob -s enabled=true \
  -s email=bob@localtest.me -s emailVerified=true \
  -s firstName=Bob -s lastName=Test
${KC} set-password -r scout --username bob --new-password bob-pw

# Add alice to scout-user (bob stays out)
ALICE_ID=$(${KC} get users -r scout -q username=alice --fields id --format csv --noquotes | tail -1)
GROUP_ID=$(${KC} get groups -r scout -q search=scout-user --fields id --format csv --noquotes | tail -1)
${KC} update users/${ALICE_ID}/groups/${GROUP_ID} -r scout -n
```

Sanity-check by requesting a token for alice and decoding it; the `groups`
claim should be `["scout-user"]`. Bob's token should have `groups: []` (or
the claim absent). Direct access grant bypasses the browser-xnat-access
flow gate (gates only run on the browser flow), so this verifies the
`groups` claim wiring without testing the gate — the gate is exercised
end-to-end by the agent-browser tests in step 11.

```bash
curl -sk -d "grant_type=password" -d "client_id=xnat" \
  -d "client_secret=a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6" \
  -d "username=alice" -d "password=alice-pw" -d "scope=openid" \
  https://keycloak.localtest.me/realms/scout/protocol/openid-connect/token \
  | python3 -c 'import sys,json,base64; t=json.load(sys.stdin)["id_token"].split(".")[1]; print(json.loads(base64.urlsafe_b64decode(t+"==")))'
```

## 6. Prepare XNAT for k3d

Both files in `xnat/` reference `dev03.tag.rcif.io`. The k3d test needs
`localtest.me` instead. Easiest: copy and edit, don't touch the originals.

```bash
cd ../xnat
mkdir -p k3d/manifests
sed 's/dev03\.tag\.rcif\.io/localtest.me/g' xnat-values.yaml \
  > k3d/manifests/xnat-values.yaml
sed 's/dev03\.tag\.rcif\.io/localtest.me/g' openid-provider.properties \
  > k3d/manifests/openid-provider.properties
```

The properties file already ships `auto.enabled=true` — Keycloak-provisioned
users are auto-enabled on first login because the `scout-user` membership
gate runs at Keycloak (via the `browser-xnat-access` flow), not at XNAT.
The plugin has no `allowedGroups` property anyway (verified against the
1.4.1-xpl source), so a plugin-side filter wouldn't be an option even if
we wanted one.

Drop a `prefs-init.ini` next to the values file. This is XNAT's first-boot
configuration file; mounting it under `/data/xnat/home/config/` skips the
setup wizard entirely (no manual site-URL entry, no admin-password change
prompt). See:
<https://wiki.xnat.org/documentation/xnat-setup-options-custom-configuration-settings>

```bash
cat > k3d/manifests/prefs-init.ini <<'EOF'
[siteConfig]
siteUrl=https://xnat.localtest.me
siteId=scout-xnat-k3d
adminEmail=admin@localtest.me
smtpEnabled=false
initialized=true
requireLogin=true
EOF
```

Sanity-check: `xnat-values.yaml` and `openid-provider.properties` reference
`xnat.localtest.me` and `keycloak.localtest.me` (no `dev03`); the
`clientSecret` in `openid-provider.properties` matches
`keycloak_xnat_client_secret` in `ansible/inventory.k3d.yaml`;
`auto.enabled=true` is present.

## 7. Helm chart prep

```bash
cd /Users/jflavin/repos/helm-charts/helm/xnat
helm dependency update
```

## 8. Namespace + Secrets

```bash
cd -   # back to scout repo root
kubectl create namespace xnat

# OpenID provider properties (consumed by the chart's authplugins:keycloak block)
kubectl -n xnat create secret generic xnat-plugin-keycloak \
  --from-file=keycloak-provider-properties=xnat/k3d/manifests/openid-provider.properties

# First-boot site config (mounted into /data/xnat/home/config/ in step 9)
kubectl -n xnat create secret generic xnat-prefs-init \
  --from-file=prefs-init.ini=xnat/k3d/manifests/prefs-init.ini

# Local CA — mkcert's rootCA.pem if available, otherwise the openssl self-signed cert
kubectl -n xnat create secret generic local-ca \
  --from-file=rootCA.pem=xnat/k3d/certs/rootCA.pem
```

## 9. Wire JVM trust + skip-wizard config into the chart values

Two things to mount into the XNAT pod:

1. **JVM truststore for the in-pod XNAT → Keycloak HTTPS call.** The OpenID
   plugin runs an HTTPS token / userinfo call from inside the XNAT pod to
   `https://keycloak.localtest.me`. mkcert installs its root CA into the
   host trust store but does nothing for containers, so the in-pod JVM has
   to import the CA itself. The chart provides no `env:` hook on the main
   container, but Tomcat sources `$CATALINA_HOME/bin/setenv.sh` at startup
   if present — that's the seam we'll use.
2. **`prefs-init.ini` under `/data/xnat/home/config/`** so XNAT skips the
   setup wizard on first boot.

Append the following to `xnat/k3d/manifests/xnat-values.yaml`:

```yaml
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
        # 1. Build a writable cacerts that includes the bundled JVM CAs + mkcert's CA.
        SRC="${JAVA_HOME}/lib/security/cacerts"
        [ -f "$SRC" ] || SRC="${JAVA_HOME}/jre/lib/security/cacerts"
        cp "$SRC" /trust/cacerts
        chmod 644 /trust/cacerts
        keytool -importcert -trustcacerts -noprompt \
          -alias mkcert-local-ca \
          -file /ca/rootCA.pem \
          -keystore /trust/cacerts \
          -storepass changeit
        # 2. Drop a setenv.sh into Tomcat's bin so JAVA_OPTS picks up the new store.
        #    The chart's home-init runs after us and merges /usr/local/tomcat/* into
        #    /TOMCAT — stock Tomcat doesn't ship setenv.sh, so ours survives.
        mkdir -p /tomcat/bin
        cat > /tomcat/bin/setenv.sh <<'EOF'
        export JAVA_OPTS="-Djavax.net.ssl.trustStore=/etc/scout-trust/cacerts -Djavax.net.ssl.trustStorePassword=changeit ${JAVA_OPTS:-}"
        EOF
        chmod +x /tomcat/bin/setenv.sh
    volumeMounts:
      - name: local-ca
        mountPath: /ca
        readOnly: true
      - name: jvm-trust
        mountPath: /trust
      - name: tomcat
        mountPath: /tomcat
```

Notes on the moving parts:

- The `tomcat` volume in the last `volumeMount` is the chart-declared emptyDir
  (templates/statefulset.yaml:209) — same one home-init uses. We don't
  redeclare it; we just reference it by name.
- `${JAVA_HOME}/lib/security/cacerts` is the Java 9+ layout;
  `${JAVA_HOME}/jre/lib/security/cacerts` is the Java 8 fallback. The script
  picks whichever exists.
- Reusing `xnatworks/xnat-web:1.9.2.1` as the init container image guarantees
  `keytool` and the same `cacerts` shape as the main container, at the cost
  of one image pull (cached after first deploy).
- If the openssl fallback was used (no mkcert), `rootCA.pem` is the
  self-signed leaf itself — `keytool` accepts that as a trust anchor and the
  same flow works.
- The `prefs-init.ini` mount uses `subPath` so it lands as a single file
  inside the existing `/data/xnat/home/config/` directory rather than
  shadowing it.

## 10. Helm install

```bash
helm install xnat /Users/jflavin/repos/helm-charts/helm/xnat \
  --kubeconfig "$KUBECONFIG" \
  --namespace xnat \
  --values xnat/k3d/manifests/xnat-values.yaml
```

Watch the StatefulSet:

```bash
kubectl -n xnat get pods -w
kubectl -n xnat describe pod xnat-0 | grep -A2 "Init Containers"
kubectl -n xnat get cluster
```

Init containers run in order: `wait-for-postgres` → `install-openid-plugin`
(curls the JAR from Bitbucket — needs internet from the host, since k3d
inherits host networking for egress) → chart's `home-init` → `xnat`.

First boot does Hibernate `hbm2ddl.auto=update` (2–5 min). If the startup
probe fires before Hibernate finishes:

```bash
helm upgrade xnat /Users/jflavin/repos/helm-charts/helm/xnat \
  --kubeconfig "$KUBECONFIG" \
  --namespace xnat \
  --reuse-values \
  --set probes.startup.failureThreshold=60
```

Plugin loaded:

```bash
kubectl -n xnat logs sts/xnat -c xnat | grep -i openid-auth-plugin
```

Verify the wizard was bypassed (should return the homepage, not the
setup-wizard page):

```bash
curl -sk https://xnat.localtest.me/ | grep -iE 'setup|initialize' || echo "wizard bypassed"
```

## 11. Run the automated login tests

The script drives two OIDC logins through `agent-browser` and asserts the
outcome of each:

- **alice** (in `scout-user`) → completes Keycloak login → lands on the
  XNAT homepage authenticated. Pass condition: page contains a logged-in
  marker (e.g., the user menu) and **does not** contain a "disabled
  account" or login-form marker.
- **bob** (not in `scout-user`) → completes Keycloak login → is rejected.
  Pass condition: page **does not** show the logged-in homepage; it shows
  a denial / disabled / back-to-login state.

Save as `xnat/k3d/test-login.sh` and `chmod +x`:

```bash
#!/usr/bin/env bash
set -euo pipefail

XNAT_URL="https://xnat.localtest.me"
KC_URL="https://keycloak.localtest.me"

# If you skipped `mkcert -install` (or used the openssl fallback), set
# AB_INSECURE=1 to disable cert verification in agent-browser.
AB_OPTS=()
if [[ "${AB_INSECURE:-0}" == "1" ]]; then
  AB_OPTS+=(--ignore-https-errors)
fi

run_case() {
  local user="$1" pw="$2" expect="$3"   # expect = "allow" | "deny"
  local session="login-${user}"

  agent-browser --session "$session" "${AB_OPTS[@]}" close --all || true

  agent-browser --session "$session" "${AB_OPTS[@]}" open "$XNAT_URL"
  agent-browser --session "$session" "${AB_OPTS[@]}" find text "Sign in with Scout" click
  agent-browser --session "$session" "${AB_OPTS[@]}" wait 'input[name="username"]'
  agent-browser --session "$session" "${AB_OPTS[@]}" fill 'input[name="username"]' "$user"
  agent-browser --session "$session" "${AB_OPTS[@]}" fill 'input[name="password"]' "$pw"
  agent-browser --session "$session" "${AB_OPTS[@]}" find role button click --name "Sign In"

  # Give XNAT time to redirect back and finish the OIDC callback.
  agent-browser --session "$session" "${AB_OPTS[@]}" wait 4000

  local url body
  url=$(agent-browser --session "$session" "${AB_OPTS[@]}" get url)
  body=$(agent-browser --session "$session" "${AB_OPTS[@]}" get text body)

  echo "[$user] landed on: $url"

  if [[ "$expect" == "allow" ]]; then
    # Allow path: must be on xnat.localtest.me, no login form, no disabled error.
    [[ "$url" == "$XNAT_URL"/* ]] \
      || { echo "FAIL [$user]: expected to land on XNAT, got $url"; return 1; }
    grep -qiE 'disabled|locked|not enabled|access denied' <<<"$body" \
      && { echo "FAIL [$user]: saw rejection text on what should be a successful login"; return 1; }
    grep -qiE 'sign in with scout|please log in' <<<"$body" \
      && { echo "FAIL [$user]: still on the login page after OIDC round-trip"; return 1; }
    echo "PASS [$user]: logged in as scout-user member"
  else
    # Deny path: must NOT be on the authenticated XNAT homepage. Accept
    # either a Keycloak error page, an XNAT login page (kicked back), or
    # an explicit "disabled" page.
    if [[ "$url" == "$XNAT_URL"/* ]] \
       && ! grep -qiE 'disabled|locked|not enabled|access denied|sign in' <<<"$body"; then
      echo "FAIL [$user]: non-member appears to be logged in (url=$url)"; return 1
    fi
    echo "PASS [$user]: non-member was rejected"
  fi
}

run_case alice alice-pw allow
run_case bob   bob-pw   deny

echo
echo "All login assertions passed."
```

Run it:

```bash
xnat/k3d/test-login.sh
# or, if you skipped `mkcert -install`:
AB_INSECURE=1 xnat/k3d/test-login.sh
```

Exit code 0 = both assertions passed. Non-zero exit = the integration is
broken in a way reproducible on dev03 once the network's back; the same
realm template, provider properties, chart values, and group-gating
configuration are in play there.

If only **bob's** case fails (he's let in), the most likely cause is that
the `browser-xnat-access` flow override on the xnat client didn't land
during the realm import. Run-all.sh asserts this in step 5; if you're
running phases manually, check
`kcadm.sh get clients/<xnat-cid> | jq .authenticationFlowBindingOverrides`
— it should show `{"browser": "<flow-id>"}`. If the field is empty,
keycloak-config-cli failed to apply the override; check its logs and
re-run the realm import.

## 12. Teardown

```bash
xnat/k3d/teardown-cluster.sh
```

Deletes the cluster and the kubeconfig file. Certs under `xnat/k3d/certs/`
are left in place so re-running `create-cluster.sh` is fast. The
`agent-browser` session state under `~/.agent-browser/sessions/login-*`
persists across runs but is overwritten on the next test — clear it
manually with `agent-browser --session login-alice close --all` if you
want a clean slate.

## What this slice deliberately doesn't test

- oauth2-proxy bypass at the ingress layer (no oauth2-proxy is running, so
  there's nothing to bypass)
- Scout's full Traefik configuration (catch-all ingress, security-headers
  middleware, redirect-to-launchpad)
- Air-gapped plugin delivery / Harbor / Nexus
- DICOM ingest paths
- Multi-replica / HA XNAT (single-node k3d, single-replica StatefulSet)
- Differentiated XNAT roles per Scout group (the runbook gates on
  `scout-user` membership only; if Scout grows `scout-admin` /
  `scout-readonly` tiers, the gate becomes inadequate and you'd need a
  group→XNAT-role mapping mechanism — likely a different XNAT plugin,
  since 1.4.1-xpl has no role mapping at all)

When dev03 is reachable again, re-run the full `xnat-deployment-runbook.md`
flow there. The `ansible/inventory.k3d.yaml`, `playbooks/dev-tls.yaml`,
`playbooks/auth-keycloak-only.yaml`, and the `xnat/k3d/` tree added for
this slice are dev-only and can stay — they don't affect the production
deploy paths.

> Note: `ansible/.gitignore` excludes `inventory*.yaml`, so
> `inventory.k3d.yaml` is local-only by default. If you want to commit it
> alongside the playbooks, use `git add -f ansible/inventory.k3d.yaml`
> (the file ships only dev placeholders, no real secrets).
