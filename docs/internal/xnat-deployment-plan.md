# XNAT Deployment Plan (Dev Scout)

A working plan for adding an XNAT instance to a development Scout cluster. The goal is to get a single-node dev XNAT running, integrated with Scout's existing Keycloak via the XNAT OpenID Auth Plugin. **Initial deployment is a manual `helm` + `kubectl` workflow** — we will not build an Ansible role until the moving parts have settled.

> Status: draft. Not an ADR. Open questions / decisions are tracked at the bottom.

## Findings

### Helm chart (NrgXnat/helm-charts)

Inspected at `/Users/jflavin/repos/helm-charts/helm/xnat/`:

- `Chart.yaml` `appVersion: 1.9.2.1`, chart `version: 1.0.1`. There is **no published Helm repository** for this chart that I could find (no `helm repo add` URL in the README, no OCI registry referenced); the chart appears to be intended for local clone-and-install. We'll deploy from the local clone.
- Subchart deps:
  - Bitnami `redis` 17.11.3 — gated by `redis.enabled` (default `true`).
  - `bokysan/postfix` `mail` 3.6.1 — **no `condition:` field**, so it is always installed by `helm dep update`. Disabling it requires either a values override on the postfix subchart itself (e.g. `mail.replicaCount: 0` if supported) or forking the chart.
- Operator prereqs: CloudNativePG (Scout has it) and ActiveMQ Artemis Operator (we don't — only needed when `activemq.broker.enabled: true`).
- Workload is a `StatefulSet`. (`templates/_deployment.yaml` exists but is a partial — the leading underscore means Helm doesn't render it.)
- Default PVCs (per-replica): `xnatdata` 100Gi, `build` 100Gi, `cache` 1Ti, `archive` 100Gi, `prearchive` 1Ti — all `ReadWriteMany`.
- `cnpg.cluster.enabled: true` by default — the chart provisions XNAT's own CNPG `Cluster`. Convenient.
- The chart's auth-plugin support (`authplugins:` in values) only mounts a properties-file Secret (named `xnat-plugin-<key>`, key `<provider>-provider-properties`) into `/data/xnat/home/config/auth/<provider>-provider.properties`. **It does not install the plugin JAR.** We have to deliver the JAR ourselves via the chart's `initContainers` extension hook.

### What the chart actually wires into XNAT vs. what it just deploys

This was the surprising finding. The only thing the chart templates into `xnat-conf.properties` (the XNAT config secret) is:

- Postgres connection: driver, URL, username, password.
- Hibernate dialect / `hbm2ddl.auto=update` / second-level cache flags.
- ActiveMQ broker URL, username, password — **only when `activemq.broker.enabled: true`**.

It does **not** template anything for redis (no host, no URL) or for mail (no SMTP host). So:

- The bundled **redis** subchart is deployed but never referenced by XNAT's config. Either XNAT auto-discovers via a hardcoded service name (unlikely — the release name is part of the service DNS), expects redis details via env vars or `extraConfig`, or doesn't actually use it in single-node mode. Either way, the chart-level cost of "use Scout's Valkey" is zero — if we ever need to wire redis up, we'd be adding `extraConfig` keys regardless of whether the bundled redis or Scout's Valkey is the target.
- The bundled **postfix** subchart is deployed for XNAT to use as an SMTP relay, but XNAT's mail config is set inside XNAT (Site Admin UI → Mail) or via `extraConfig`. The subchart contributes a running mail container; nothing more.

### XNAT image (Docker Hub)

`xnatworks/xnat-web` has a `1.10.0` tag (pushed ~3 days before this writing) and a `1.10.1-SNAPSHOT`. The chart's `appVersion` is `1.9.2.1`; first deploy will pin to that, then bump.

### XNAT OpenID Auth Plugin

From <https://bitbucket.org/xnatx/openid-auth-plugin>:

- Latest published artifact: `openid-auth-plugin-1.4.1-xpl.jar` (2025-12-12, ~879 KB).
- Direct download: `https://bitbucket.org/xnatx/openid-auth-plugin/downloads/openid-auth-plugin-1.4.1-xpl.jar`. Bitbucket downloads are public — `curl -fLO` works without auth.
- README states "XNAT 1.7.5.x+". 1.10 isn't called out specifically; we'll verify on first run.
- Configuration goes in a `<provider>-provider.properties` file under `/data/xnat/home/config/auth/`. The plugin's own `src/main/resources/openid-provider-sample-Keycloak.properties` is a verbatim Keycloak example.

Properties we'll set (parameterized for Scout):

```properties
auth.method=openid
type=openid
provider.id=keycloak
name=Scout SSO
auto.enabled=false
auto.verified=true

# Public XNAT URL — see "siteUrl method" below
siteUrl=https://xnat.{server_hostname}
preEstablishedRedirUri=/openid-login

openid.keycloak.clientId=xnat
openid.keycloak.clientSecret=<from Keycloak realm>
openid.keycloak.accessTokenUri=https://keycloak.{server_hostname}/realms/scout/protocol/openid-connect/token
openid.keycloak.userAuthUri=https://keycloak.{server_hostname}/realms/scout/protocol/openid-connect/auth
openid.keycloak.userInfoUri=https://keycloak.{server_hostname}/realms/scout/protocol/openid-connect/userinfo
openid.keycloak.scopes=openid,profile,email
openid.keycloak.shouldFilterEmailDomains=false
openid.keycloak.forceUserCreate=true
openid.keycloak.emailProperty=email
openid.keycloak.givenNameProperty=given_name
openid.keycloak.familyNameProperty=family_name
openid.keycloak.pkceEnabled=true
openid.keycloak.usernamePattern=[providerId]-[sub]
openid.keycloak.link=<a href="/openid-login?providerId=keycloak">Sign in with Scout</a>
```

## Architectural decisions

### Where the Keycloak client_id and client_secret come from

These are not values you "obtain" from a running Keycloak — they're values **Scout creates in Keycloak** through the realm-import workflow. The pattern (used today by every other Scout service that talks OIDC) is:

1. Pick a `client_id` — for us, `xnat`. This becomes the value of `keycloak_xnat_client_id` in inventory.
2. Generate a `client_secret` locally: `openssl rand -hex 16` (vault-encrypt for non-dev), stored in inventory as `keycloak_xnat_client_secret`.
3. Add an `xnat` client block to `ansible/roles/keycloak/templates/scout-realm.json.j2` that references both vars.
4. Re-run `make install-auth`. Keycloak Config CLI imports the realm and creates the client with that exact `clientSecret`.
5. The same `client_secret` value is what we paste (or templated) into the XNAT plugin's `openid.keycloak.clientSecret`.

So the realm-template change is a **prerequisite** to deploying XNAT — it's the only piece that *cannot* stay manual through `kubectl`. We touch the realm template + inventory secrets + run `make install-auth`, then proceed with the manual XNAT deploy.

### Keycloak roles — cosmetic now, plus an XNAT-side access gate

**Background.** Keycloak has realm roles (visible to every client) and client roles (scoped to a single client). Groups bundle role mappings — that's how Scout's `scout-admin` group simultaneously assigns `superset_admin`, `grafana-admin`, `jupyterhub-admin`, etc. Roles ride into the JWT as claims, and consuming services authorize from those claims (Superset reads `superset_admin`, etc.).

**XNAT does not consume Keycloak roles.** The OpenID Auth Plugin authenticates the user and creates/finds an XNAT user; it does not map IdP claims to XNAT permissions. XNAT 1.x has no first-class facility for "Keycloak role X → XNAT permission Y" — XNAT's authorization is internal (XNAT users, XNAT groups, the site-admin flag, project-level roles). So `xnat-user` / `xnat-admin` client roles, if defined, would appear in the JWT but no XNAT code path would inspect them. **Cosmetic.**

**The access-control gap.** Without oauth2-proxy in front, and with Keycloak roles inert from XNAT's perspective, the gate degrades to: *anyone who completes a Keycloak login can hit XNAT.* That's looser than every other Scout service, where `scout-user` membership is the real gate.

**Mitigation: `auto.enabled=false` in the plugin properties.** With `auto.enabled=false`, new XNAT users created on first OIDC login land **disabled**; an XNAT admin must enable them inside XNAT before they can do anything. This is XNAT's native user-approval workflow — functionally parallel to Scout's `scout-user` gate, just enforced inside XNAT rather than at the ingress.

**Plan:**

- Set `auto.enabled=false` in `openid-provider.properties`. Document the consequence: a Scout admin who is also an XNAT admin must approve each new XNAT user.
- Still add `xnat-user` and `xnat-admin` client roles to the realm template, with `scout-user` → `xnat-user` and `scout-admin` → `xnat-admin` group mappings. **Be explicit these are inert today** — XNAT doesn't read them. They cost nothing, keep the realm shape consistent with every other Scout service, and give us a documentation hook the next time someone asks "what roles does XNAT have?"

This is reversible — if we later add an XNAT plugin that *does* map JWT roles to XNAT permissions, the role definitions and group mappings are already in place.

### Redis — don't deploy the bundled Bitnami redis

Set `redis.enabled: false`. The chart wires nothing into XNAT's config from redis anyway, so disabling it costs us nothing observable up front. If something inside XNAT 1.9.x/1.10.x silently expects redis (e.g. for Hibernate L2 cache backing or Spring Session), we'll see it in the logs and decide whether to:

- Point XNAT at Scout's central Valkey via `extraConfig` keys (estimated work: research the exact `xnat.*` / Spring Session property names — not documented in the chart; ask in the XNAT slack or read xnat-web source). Wire compatibility is a non-issue (Valkey is RESP-compatible). NetworkPolicy may need a tweak so the `xnat` namespace can reach `valkey.valkey.svc.cluster.local`.
- Or fall back to enabling the bundled redis, which is cheap and self-contained.

This way we don't pay the Valkey-integration cost until we know XNAT actually needs redis.

### Mail / postfix — leave it alone for the first deploy

Postfix is a small SMTP relay container (`bokysan/docker-postfix`). XNAT can be pointed at it for outbound mail (registration emails, password resets, project notifications). The chart deploys postfix but doesn't tell XNAT to use it.

For the first deploy, we'll let postfix install (the subchart has no `condition:` — disabling means a chart fork or a values-override gymnastic) and not configure XNAT's mail at all. XNAT will fail silently on email sends, which is fine for dev. As a follow-up, we can either point postfix's `RELAYHOST` at MailHog (Scout's existing dev SMTP), or set XNAT's SMTP host (in the Site Admin UI or via `extraConfig`) directly to `mailhog.mailhog.svc.cluster.local:1025` and ignore the postfix container.

### ActiveMQ Artemis — leave disabled

`activemq.broker.enabled: false`. ActiveMQ Artemis is the JMS broker XNAT uses for **distributed async messaging across nodes**: cross-node event propagation, the container-service job queue, the XNAT messaging bus, and similar Spring-JMS-driven internals. With `replicaCount: 1` XNAT falls back to in-process queues and we lose nothing observable.

What we'd lose by skipping it:

- Multi-replica/HA XNAT — events emitted on one pod wouldn't reach the others. Single-replica dev is unaffected.
- Some workflows that explicitly rely on a JMS broker may log warnings or run inline instead of async. None block core XNAT functionality.

What we save:

- Installing the ActiveMQ Artemis Operator (a separate operator we don't already run in Scout).
- The `ActiveMQArtemis` CR, its broker pod, ConfigMap, and Service.

When we move toward production / multi-replica XNAT this needs revisiting — likely in concert with adding either the Artemis Operator to Scout or pointing XNAT at an existing broker. Out of scope for this dev pass.

### siteUrl method for reverse-proxy / OIDC redirects

We will use the **`siteUrl` properties-file method** (your choice). The plugin builds redirect URIs from `siteUrl + preEstablishedRedirUri` (default `/openid-login`), so setting `siteUrl=https://xnat.{server_hostname}` is sufficient for the OIDC flow.

We will *not* inject a Tomcat `setenv.sh` Secret in the first pass. If we hit places where XNAT generates URLs from the request (download links, REST self-references) and they leak the in-pod hostname/scheme, we'll add the `setenv.sh` extras at that point.

### OAuth2 Proxy — bypass it

Place the XNAT ingress on the oauth2-proxy *bypass* list (alongside `auth.{hostname}` and `keycloak.{hostname}`). Reasons:

- The OpenID plugin runs an interactive OIDC code flow inside XNAT itself. Putting another OIDC dance in front would mean two redirects to Keycloak, two cookie domains, and (potentially) two distinct user identities for one Keycloak login.
- The XNAT REST API and the DICOM C-STORE listener authenticate with their own credentials/sessions. Browser-cookie middleware breaks non-browser clients (XNAT desktop uploaders, container-service callbacks, anything using XNAT's API tokens, DICOM SCUs).
- OAuth2 Proxy can't usefully *protect* XNAT either — XNAT does its own auth, and the bypass model is what Scout's existing browser-OIDC services (Keycloak admin) already use.

(I removed the earlier "orthanc and dcm4chee do this" framing — they're test fixtures, not part of the canonical Scout deploy, so they don't constitute precedent. The reasoning above stands on its own.)

### Plugin JAR delivery (init container downloads from Bitbucket)

Per your direction: an init container `curl`s the JAR from Bitbucket at deploy time and drops it in the chart's `home-plugins` emptyDir. The chart already has an `initContainers:` extension hook that runs before its own `home-init` container, and `home-plugins` is a shared emptyDir, so this works out of the box.

```yaml
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
```

**Limitation (documented):** this requires internet egress at deploy time and won't work in air-gapped Scout. The air-gapped story is to vendor the JAR (e.g. host it in Nexus per ADR 0017, or as a `binaryData` ConfigMap, or build a tiny installer image). Not solving this now.

### DICOM ingest — out of scope for this pass; DICOMweb when we need it

Skip DICOM in the first deploy; default `service.type: ClusterIP` (no exposed C-STORE listener). When we want DICOM ingest, **DICOMweb is the assumed path** — it's HTTP under XNAT's normal web port and is already reachable at `https://xnat.{server_hostname}/xapi/dicomweb/...`. No extra ingress, no extra port, works for any client that speaks DICOMweb (Orthanc does, many clinical PACSes do).

DIMSE C-STORE is the legacy alternative we'd reach for if we hit a client that doesn't do DICOMweb. Quick summary so it's recorded but doesn't sprawl: DIMSE is raw TCP, not HTTP, so it can't be subdomain-routed by `Host` header — exposure is via `NodePort` / `LoadBalancer` (`<host>:<port>` + AE title), or via Traefik `IngressRouteTCP` with TLS+SNI for hostname-based multiplexing (works only for SCUs that speak DICOM-TLS with SNI). The chart's existing `dicom_scp` block does the NodePort case. For mock-PACS testing (Orthanc → XNAT), neither approach is needed — both pods talk over the cluster network.

### Storage — `local-path` for dev, with a caveat

Storage class `local-path`, `ReadWriteOnce`, sized for dev (~10 Gi each PVC). The k3s `local-path` provisioner is hostPath-backed: a PV created on node A is **only readable by pods scheduled on node A**. For a single-replica StatefulSet on a single-node dev cluster this is fine.

Caveat to flag for the on-prem story: in a multi-node on-prem cluster, the XNAT pod will be **pinned to whichever node first attached its PVCs**. If that node goes down, the StatefulSet won't reschedule elsewhere until the PVs are migrated. A real on-prem deployment will need either:

- A networked filesystem (NFS / Ceph / Longhorn) for the XNAT data volumes, with a corresponding `ReadWriteMany`-capable StorageClass; or
- An explicit accept-the-pinning posture (XNAT is single-replica anyway), with a documented recovery procedure when the pinned node fails.

Resolving this is the same problem ADR 0004 already touched on; it should be folded into that conversation rather than a new XNAT-specific decision.

### Mock external PACS — Orthanc as a future companion

Stand up an Orthanc instance to act as a mock external PACS/VNA for "store data into XNAT" testing. Scout already has an `orthanc` Ansible role and a `make install-orthanc` target — it's a test fixture, not part of the main deploy. Two options when we want it:

- **In-cluster:** `make install-orthanc` (existing). Orthanc-on-pod can C-STORE to XNAT-on-pod over the cluster network — no ingress needed for the storage path.
- **On the staging node:** Closer to how a real external PACS would live (off-cluster). Worth doing once we want to exercise the firewall/routing path that prod-XNAT will use.

Out of scope here; just noting the destination.

## Manual deployment runbook (proposed)

> All commands run from the jump host with `kubectl` / `helm` configured against the dev cluster. Substitute `{server_hostname}`. We will not script this yet; the goal is to learn the moving parts before automating.

### Prereqs

- CloudNativePG operator already installed (it is, via Scout's Postgres role).
- Keycloak realm has an `xnat` client (see step 1).
- Traefik ingress controller already running.

### 1. Add the `xnat` Keycloak client

Edit `ansible/roles/keycloak/templates/scout-realm.json.j2` to add an `xnat` client. Edit inventory to add:

```yaml
keycloak_xnat_client_id: xnat
keycloak_xnat_client_secret: $(openssl rand -hex 16 | ansible-vault encrypt_string ...)
```

Required fields on the client:

- `clientId: xnat`, `secret: {{ keycloak_xnat_client_secret }}`.
- `protocol: openid-connect`, `publicClient: false`, `standardFlowEnabled: true`, `directAccessGrantsEnabled: false`.
- `redirectUris: ["https://xnat.{server_hostname}/openid-login*"]` (start permissive, tighten later).
- `webOrigins: ["https://xnat.{server_hostname}"]`.
- `attributes: { "pkce.code.challenge.method": "S256" }`.
- Default client scopes: `openid`, `profile`, `email`.
- Client roles: `xnat-user` and `xnat-admin` (cosmetic — see "Keycloak roles" decision above).
- Realm group mappings: add `xnat-user` to `scout-user`'s clientRoles for the `xnat` client; add `xnat-admin` to `scout-admin`'s clientRoles for the `xnat` client (mirror the existing entries for other services in `scout-realm.json.j2`).

Then: `make install-auth` to apply the realm change.

### 2. Helm-prep the chart

```bash
cd /Users/jflavin/repos/helm-charts/helm/xnat
helm dependency update     # downloads bitnami redis + bokysan postfix
```

### 3. Create namespace and supporting objects

```bash
kubectl create namespace xnat
```

Create the OpenID provider-properties Secret. The chart expects it at name `xnat-plugin-keycloak`, key `keycloak-provider-properties` (the chart loops `range $plugin, $p := .Values.authplugins` — `$plugin` is the map key, `$p.provider` is the property file name).

```bash
kubectl -n xnat create secret generic xnat-plugin-keycloak \
  --from-file=keycloak-provider-properties=./openid-provider.properties
```

…where `openid-provider.properties` is the file from the "Findings" section, with `siteUrl`, `clientSecret`, and the three Keycloak `*Uri` values filled in.

### 4. Write `xnat-values.yaml`

A minimal values file capturing every decision above:

```yaml
replicaCount: 1

image:
  repository: xnatworks/xnat-web
  tag: "1.9.2.1"  # bump to 1.10.0 in step 7

redis:
  enabled: false

activemq:
  broker:
    enabled: false

cnpg:
  cluster:
    enabled: true
    instances: 1
    storage:
      size: 10Gi
      storageClass: local-path
    credentials:
      database: xnat
      username: xnat
      password: <generated>

ingress:
  enabled: true
  annotations:
    kubernetes.io/ingress.class: traefik
  hosts:
    - host: xnat.{server_hostname}
      paths:
        - path: /
          pathType: Prefix
  # NOTE: deliberately no oauth2-proxy middleware annotations — see decision above

# Five PVCs, all overridden for dev
volumes:
  xnatdata:
    accessMode: ReadWriteOnce
    storageClass: local-path
    size: 10Gi
  build:
    accessMode: ReadWriteOnce
    storageClass: local-path
    size: 5Gi
  cache:
    accessMode: ReadWriteOnce
    storageClass: local-path
    size: 10Gi
  archive:
    accessMode: ReadWriteOnce
    storageClass: local-path
    size: 10Gi
  prearchive:
    accessMode: ReadWriteOnce
    storageClass: local-path
    size: 5Gi

# OpenID auth-plugin properties file mount (chart-provided)
authplugins:
  keycloak:
    provider: keycloak
    auth: true

# Init container to fetch the OpenID plugin JAR at deploy time
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
```

> Note: this requires internet egress from the cluster nodes at deploy time. **Won't work air-gapped** — air-gap path is to vendor the JAR (Nexus per ADR 0017, ConfigMap, or installer image). Document only; don't solve here.

> Note: We're leaving `mail` (postfix subchart) at chart defaults — postfix will install but XNAT won't be configured to use it. For dev, that's fine; XNAT email sends will fail silently. Wiring XNAT to MailHog is a follow-up.

### 5. `helm install`

```bash
helm install xnat /Users/jflavin/repos/helm-charts/helm/xnat \
  --namespace xnat \
  --values xnat-values.yaml
```

### 6. First-boot verification

- Watch the StatefulSet come up: `kubectl -n xnat get pods -w`. Init containers run in order: `wait-for-postgres` → our `install-openid-plugin` → chart's `home-init` → `xnat` container.
- First boot does Hibernate `hbm2ddl.auto=update` schema bootstrap — expect 2–5 min. The chart's `startupProbe` allows up to ~150s; if Hibernate takes longer, the pod will be killed and restarted. Bump `probes.startup.failureThreshold` if needed.
- `kubectl -n xnat logs sts/xnat -c xnat` and grep for `openid-auth-plugin` to verify the plugin loaded.
- Browse to `https://xnat.{server_hostname}` — first time you'll get the XNAT setup wizard. Set site URL, admin email, etc., then log in as `admin`/`admin`, change the admin password.
- Log out. Reload — you should see a "Sign in with Scout" link from `openid.keycloak.link`. Click it, complete the Keycloak flow.
- Because `auto.enabled=false`, the plugin will create a **disabled** XNAT user `keycloak-<sub>` and bounce you back to the login page. Log in again as the local `admin`, navigate to the XNAT user admin UI, find the new user, enable them, and promote to site admin if appropriate. Log out, log back in via Keycloak — now access works. This same approval step is what every subsequent user goes through.

### 7. Bump to 1.10.0

After the 1.9.2.1 deploy is healthy:

```bash
helm upgrade xnat /Users/jflavin/repos/helm-charts/helm/xnat \
  --namespace xnat \
  --reuse-values \
  --set image.tag=1.10.0
```

Watch logs for any startup-probe / config-key regressions; fold any required values changes into a follow-up note.

## Open questions / outstanding decisions

1. **Mail wiring:** once we know we want it, point postfix's `RELAYHOST` at MailHog, or skip postfix and configure XNAT's SMTP directly at `mailhog.mailhog.svc.cluster.local:1025`?
2. **Redis-driven feature surface:** do we hit any XNAT functionality (Hibernate L2 cache, Spring Session, container service polling) that breaks with `redis.enabled: false`? Plan: deploy and grep logs. If we do break things, the next decision is bundled redis vs. Valkey.
3. **Air-gapped plugin delivery:** which path (Nexus mirror, vendored binary, installer image)? Defer until we work the air-gap story for XNAT broadly.
4. **Mock PACS:** Orthanc in-cluster vs. on the staging node? In-cluster is simpler for dev; staging-node placement is a better rehearsal for prod ingest paths.
5. **PV story for on-prem:** doesn't block dev, but we should not ship XNAT to on-prem clusters before resolving — should fold into the ADR 0004 conversation.
