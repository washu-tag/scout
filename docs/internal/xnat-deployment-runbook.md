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
- [ ] CloudNativePG operator running: `kubectl --kubeconfig=$KUBECONFIG get pods -n cnpg-system`
- [ ] Traefik running: `kubectl --kubeconfig=$KUBECONFIG -n kube-system get pods | grep traefik`
- [ ] DNS for `xnat.dev03.tag.rcif.io` resolves to the cluster ingress IP

## 1. Update Keycloak realm

The realm template (`ansible/roles/keycloak/templates/scout-realm.json.j2`) and dev03 inventory have already been updated in this branch with the `xnat` Keycloak client, `xnat-user` / `xnat-admin` client roles, and `scout-user` / `scout-admin` group mappings. The client_id default lives in `ansible/roles/scout_common/defaults/main.yaml`; the secret is in `ansible/inventory.dev03.yaml`.

- [ ] Apply the realm to dev03:

  ```bash
  cd ansible
  make install-auth INVENTORY_FILE=inventory.dev03.yaml
  ```

- [ ] Verify in the Keycloak admin UI at https://keycloak.dev03.tag.rcif.io:
  - Realm `scout` → Clients → `xnat` exists
  - Credentials tab → secret matches `keycloak_xnat_client_secret` from `inventory.dev03.yaml`
  - Settings → Valid Redirect URIs includes `https://xnat.dev03.tag.rcif.io/openid-login*`
  - Groups → `scout-user` and `scout-admin` → Role Mappings → Client Roles for `xnat` shows `xnat-user` / `xnat-admin` respectively

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

## 3. Namespace and provider-properties Secret

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

## 4. Helm install (1.9.2.1)

- [ ] Install:

  ```bash
  helm install xnat /Users/jflavin/repos/helm-charts/helm/xnat \
    --kubeconfig $KUBECONFIG \
    --namespace xnat \
    --values /Users/jflavin/apps/scout/.worktrees/xnat-dev-wt/xnat/xnat-values.yaml
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

- [ ] OpenID plugin loaded:

  ```bash
  kubectl --kubeconfig=$KUBECONFIG -n xnat logs sts/xnat -c xnat | grep -i openid-auth-plugin
  ```

- [ ] Hibernate `hbm2ddl.auto=update` schema bootstrap completes (2–5 min on first boot). If `xnat-0` keeps restarting during init because the chart's startup probe (~150s) fires first, bump `probes.startup.failureThreshold` in `xnat-values.yaml` and `helm upgrade ... --reuse-values --set probes.startup.failureThreshold=60`:

  ```bash
  kubectl --kubeconfig=$KUBECONFIG -n xnat logs sts/xnat -c xnat --tail=200
  ```

- [ ] Browse https://xnat.dev03.tag.rcif.io — XNAT setup wizard appears.
- [ ] Complete the wizard (set site URL = `https://xnat.dev03.tag.rcif.io`, set admin email, etc.). Log in as `admin` / `admin` and change the admin password immediately.
- [ ] Log out, reload — confirm the "Sign in with Scout" link appears (driven by `openid.keycloak.link`).
- [ ] Click the Scout link, complete the Keycloak flow as a user in `scout-user`. Because `auto.enabled=false`, the plugin creates the XNAT user `keycloak-<sub>` in **disabled** state and you'll get bounced back to the login page.
- [ ] Log back in as the local XNAT admin → user admin UI → find the new `keycloak-<sub>` user → enable them (and promote to site admin if desired).
- [ ] Log out, log back in via Keycloak — full access this time. **This same approval step gates every new XNAT user.**

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
