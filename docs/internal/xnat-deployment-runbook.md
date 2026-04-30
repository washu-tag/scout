# XNAT Dev03 Deployment Runbook

Companion to `xnat-deployment-plan.md`. Concrete commands for the manual XNAT deploy on the dev03 Scout cluster.

All cluster-touching commands use this kubeconfig — export it once at the start of the session:

```bash
export KUBECONFIG=/Users/jflavin/apps/scout/.worktrees/xnat-dev-wt/.kube/scout/tagdev-control-03/config
```

Working directory is the Scout repo root (`/Users/jflavin/apps/scout/.worktrees/xnat-dev-wt`) unless otherwise noted. The XNAT helm chart lives at `/Users/jflavin/repos/helm-charts/helm/xnat`.

## 0. Prereqs

- [ ] `helm` and `kubectl` installed locally
- [ ] kubeconfig resolves: `kubectl --kubeconfig=$KUBECONFIG get nodes`
- [ ] CloudNativePG operator running: `kubectl --kubeconfig=$KUBECONFIG -n scout-operators get pods | grep cnpg`
- [ ] Traefik running: `kubectl --kubeconfig=$KUBECONFIG -n kube-system get pods | grep traefik`
- [ ] DNS for `xnat.dev03.tag.rcif.io` resolves to the cluster ingress IP

## 1. Update Keycloak realm

The realm template (`ansible/roles/keycloak/templates/scout-realm.json.j2`) and dev03 inventory have already been updated in this branch with the `xnat` Keycloak client, `xnat-user` / `xnat-admin` / `xnat-access` client roles, the `scout-user` / `scout-admin` group mappings, and the `browser-xnat-access` authentication flow override. **These changes have not yet been deployed to dev03** — re-running `install-auth` is required for the gate to be live. The client_id default lives in `ansible/roles/scout_common/defaults/main.yaml`; the secret is in `ansible/inventory.dev03.yaml`.

- [ ] Apply the realm to dev03:

  ```bash
  cd ansible
  make install-auth INVENTORY_FILE=inventory.dev03.yaml
  ```

- [ ] Verify in the Keycloak admin UI at https://keycloak.dev03.tag.rcif.io:
  - Realm `scout` → Clients → `xnat` exists
  - Credentials tab → secret matches `keycloak_xnat_client_secret` from `inventory.dev03.yaml`
  - Settings → Valid Redirect URIs includes `https://xnat.dev03.tag.rcif.io/openid-login*`
  - Roles tab → `xnat-user`, `xnat-admin`, `xnat-access` all exist
  - Groups → `scout-user` Role Mappings → Client Roles for `xnat` shows `xnat-user` and `xnat-access` (the load-bearing one)
  - Groups → `scout-admin` Role Mappings → Client Roles for `xnat` shows `xnat-admin`
  - Authentication → Flows → `browser-xnat-access` exists with the conditional sub-flow that requires the `xnat-access` role
  - Clients → `xnat` → Advanced → Authentication Flow Overrides → Browser Flow = `browser-xnat-access`

## 2. Helm chart prep

- [ ] `cd /Users/jflavin/repos/helm-charts/helm/xnat`
- [ ] `git status` — confirm clean working tree, on the desired branch (chart `version: 1.0.1`, `appVersion: 1.9.2.1`)
- [ ] Pull subchart deps (Bitnami redis + bokysan postfix):

  ```bash
  helm dependency update
  ```

- [ ] Cross-check that the value keys we're setting actually exist in this chart's `values.yaml`. Spot-check the ones most likely to drift:

  ```bash
  yq '.cnpg' values.yaml
  yq '.volumes' values.yaml
  yq '.initContainers' values.yaml
  yq '.authplugins' values.yaml
  ```

  Compare structure to `/Users/jflavin/apps/scout/.worktrees/xnat-dev-wt/xnat/xnat-values.yaml` and adjust paths in our values file if the chart uses different keys (e.g. `cnpg.cluster.storage.size` vs `cnpg.storage.size`).

## 3. Namespace and supporting Secrets

- [ ] Create the namespace:

  ```bash
  kubectl --kubeconfig=$KUBECONFIG create namespace xnat
  ```

- [ ] Create the OpenID provider-properties Secret. Chart expects name `xnat-plugin-keycloak`, key `keycloak-provider-properties`:

  ```bash
  kubectl --kubeconfig=$KUBECONFIG -n xnat create secret generic xnat-plugin-keycloak \
    --from-file=keycloak-provider-properties=/Users/jflavin/apps/scout/.worktrees/xnat-dev-wt/xnat/openid-provider.properties
  ```

- [ ] Sanity-check that the `clientSecret` in `xnat/openid-provider.properties` matches `keycloak_xnat_client_secret` in `ansible/inventory.dev03.yaml`. If they don't match, the Keycloak login will fail with an `invalid_client` error.

- [ ] Create the `xnat-prefs-init` Secret used by the values file's `extraVolumes`/`extraVolumeMounts` to skip the first-boot setup wizard. The mounted file lands at `/data/xnat/home/config/prefs-init.ini`; XNAT consumes it on first launch and self-completes the wizard:

  ```bash
  kubectl --kubeconfig=$KUBECONFIG -n xnat create secret generic xnat-prefs-init \
    --from-file=prefs-init.ini=/Users/jflavin/apps/scout/.worktrees/xnat-dev-wt/xnat/prefs-init.ini
  ```

- [ ] Create a placeholder `postfix-password` Secret. The chart's `bokysan/postfix` mail subchart is unconditional and references a hardcoded `postfix-password` Secret; without it `xnat-mail-0` sits in `CreateContainerConfigError` and `helm install --wait` never returns. Contents are arbitrary — XNAT is configured `smtpEnabled=false` and the postfix container is never used:

  ```bash
  kubectl --kubeconfig=$KUBECONFIG -n xnat create secret generic postfix-password \
    --from-literal=username=xnat --from-literal=password=unused-dev
  ```

## 4. Helm install (1.9.2.1)

- [ ] Install. The 20-min timeout pairs with the bumped `probes.startup.failureThreshold: 60` in the values file to cover Hibernate's first-boot DDL (3–5 min):

  ```bash
  helm install xnat /Users/jflavin/repos/helm-charts/helm/xnat \
    --kubeconfig $KUBECONFIG \
    --namespace xnat \
    --values /Users/jflavin/apps/scout/.worktrees/xnat-dev-wt/xnat/xnat-values.yaml \
    --wait --timeout 20m
  ```

- [ ] First-pass health: `kubectl --kubeconfig=$KUBECONFIG -n xnat get pods` — expect `xnat-0` working through init containers, and a CNPG cluster pod (`xnat-cnpg-1` or similar).

## 5. First-boot verification

Init containers run in order: `wait-for-postgres` → our `install-openid-plugin` → chart's `home-init` → `xnat`.

- [ ] Watch pods come up:

  ```bash
  kubectl --kubeconfig=$KUBECONFIG -n xnat get pods -w
  ```

- [ ] Confirm init container ordering and exit codes:

  ```bash
  kubectl --kubeconfig=$KUBECONFIG -n xnat describe pod xnat-0 | grep -A2 "Init Containers"
  ```

- [ ] CNPG cluster healthy:

  ```bash
  kubectl --kubeconfig=$KUBECONFIG -n xnat get cluster
  ```

- [ ] Confirm the OpenID plugin loaded by querying the XNAT plugin API. XNAT's logback config only routes ERROR-level logs to stdout, so the plugin scan is invisible there — query the API instead of grepping logs:

  ```bash
  curl -sku admin:admin https://xnat.dev03.tag.rcif.io/xapi/plugins | grep '"openIdAuthPlugin"'
  ```

- [ ] Enable `keycloak` as a site auth provider. The chart leaves `enabledProviders=["localdb"]`; the keycloak provider has to be explicitly added or the `/openid-login` route won't accept it, regardless of whether the plugin loaded:

  ```bash
  curl -sku admin:admin -X POST -H 'Content-Type: application/json' \
    -d '{"enabledProviders": ["localdb", "keycloak"]}' \
    https://xnat.dev03.tag.rcif.io/xapi/siteConfig
  ```

- [ ] Hibernate `hbm2ddl.auto=update` schema bootstrap completes (3–5 min on first boot). The values file's `probes.startup.failureThreshold: 60` gives a 10-min budget. If `xnat-0` somehow still restarts during init, watch logs:

  ```bash
  kubectl --kubeconfig=$KUBECONFIG -n xnat logs sts/xnat -c xnat --tail=200
  ```

- [ ] Browse https://xnat.dev03.tag.rcif.io. Because `prefs-init.ini` was mounted, the setup wizard is already complete — you'll land on the login page directly. Default admin credentials are `admin` / `admin`; change the admin password from the user admin UI before opening up access.
- [ ] Confirm the "Sign in with Scout" link appears on the login page (driven by `openid.keycloak.link`).
- [ ] Click the Scout link, complete the Keycloak flow as a user in `scout-user`. Because `auto.enabled=true` and the user holds the `xnat-access` client role (granted transitively via `scout-user`), the plugin auto-provisions and enables the XNAT user `keycloak-<sub>` and lands you on the homepage authenticated. Group membership was already verified at Keycloak by the `browser-xnat-access` flow before any auth code was issued.
- [ ] Negative test: log in as a user *not* in `scout-user`. Keycloak's `Deny Access` step inside the `browser-xnat-access` flow fires, the access-denied page renders, and no auth code reaches XNAT. **If this fails open**, the realm import didn't apply the `authenticationFlowBindingOverrides.browser`; check keycloak-config-cli's logs and re-run the auth role.

## 6. Bump to 1.10.0

After the 1.9.2.1 deploy is stable:

- [ ] Upgrade:

  ```bash
  helm upgrade xnat /Users/jflavin/repos/helm-charts/helm/xnat \
    --kubeconfig $KUBECONFIG \
    --namespace xnat \
    --reuse-values \
    --set image.tag=1.10.0
  ```

- [ ] Watch the StatefulSet roll:

  ```bash
  kubectl --kubeconfig=$KUBECONFIG -n xnat get pods -w
  ```

- [ ] Re-run the plugin-loaded check and the Keycloak login flow from step 5.
- [ ] Update `image.tag` in `xnat/xnat-values.yaml` and commit so the values file matches running state.

## 7. Teardown (when needed)

> Destructive — confirm the right cluster before running.

- [ ] `helm uninstall xnat --kubeconfig $KUBECONFIG -n xnat`
- [ ] `kubectl --kubeconfig=$KUBECONFIG -n xnat delete cluster --all` (CNPG cluster)
- [ ] `kubectl --kubeconfig=$KUBECONFIG -n xnat delete pvc --all` (local-path PVs — irreversible data loss)
- [ ] `kubectl --kubeconfig=$KUBECONFIG delete namespace xnat`

## Known gaps (per `xnat-deployment-plan.md`)

- Postfix subchart installs but XNAT is not configured to use it — outbound mail will fail silently. Wiring XNAT → MailHog (or postfix → MailHog as relay) is a follow-up.
- `redis.enabled: false` — if XNAT logs an unexpected redis dependency, decision is bundled redis vs. Scout's Valkey.
- ActiveMQ disabled — fine for `replicaCount: 1`, revisit before HA.
- DICOM C-STORE listener not exposed (default `service.type: ClusterIP`); DICOMweb at `https://xnat.dev03.tag.rcif.io/xapi/dicomweb/...` is the assumed ingest path.
- `local-path` storage pins XNAT to whichever node first attached its PVCs — single-node-pinned, fine for dev.
- The init container that fetches the OpenID plugin JAR requires internet egress at deploy time; this won't work on air-gapped clusters as-is.
