# Deploying XNAT and Custom Plugins into Scout

## Purpose and scope

This document distills, from the `scout-xnat-deploy` POC branch (and the
earlier `xnat-dev-wt` exploration), the reusable mechanics of **deploying XNAT
into a Scout cluster with one or more plugins, plugin configuration, and XNAT
site configuration**. It deliberately generalizes past the POC's single
`scout-xnat-auth-plugin`: the same patterns install any XNAT plugin (e.g. the
off-the-shelf `xnat-openid-auth-plugin`).

It is a reference for the eventual goal: **an Ansible role that templates and
deploys XNAT + plugins the way the rest of Scout is deployed**. Wherever the POC
did something by hand (`kubectl create secret`, `helm install`), the "Toward
Ansible automation" section notes what that becomes in a role.

A guiding constraint for that future work: **keep it gitops-friendly.** Anything
that can be expressed declaratively in a Helm chart should live in a chart —
either a small wrapper chart added to the Scout repo, or changes contributed
back upstream — rather than in imperative `kubectl` steps.

This doc is self-contained: the concrete deploy steps, the plugin build notes,
and the XNAT 1.10 / JDK 21 migration findings — which on the POC branch lived in
separate companion docs — are inlined below so nothing here depends on artifacts
from the POC branch. The POC branch names (`scout-xnat-deploy`, `xnat-dev-wt`)
and any commit hashes appear only as historical provenance; **none of the
referenced source files, values files, or commits will exist on an
implementation branch cut fresh from `main`** — they describe what the POC built
and what to (re)build.

## The moving parts

A working XNAT-in-Scout deployment is four layers. The "POC form" column records
how each looked in the POC; on an implementation branch these are the things to
build:

| Layer | What it is | POC form |
| --- | --- | --- |
| **Upstream chart** | The community XNAT Helm chart (`xnatworks/xnat-web`) | An external `helm-charts` checkout (`~/repos/helm-charts/helm/xnat`), `appVersion 1.9.2.1`, chart `version 1.0.2`, referenced via a `$XNAT_CHART` env var |
| **Scout values** | A values file + a `prefs-init.ini` site-config file | Hand-maintained `xnat/xnat-values.yaml` + `xnat/prefs-init.ini` |
| **Plugin artifacts** | Plugin JAR(s) + provider properties, delivered as Secrets | A Scout-built plugin (`scout-xnat-auth-plugin/`) or a downloaded JAR, loaded via out-of-band Secrets |
| **Auth substrate** | A Keycloak `xnat` client + the oauth2-proxy edge gate | Applied via `make install-auth`. The POC also added token-forwarding / Jupyter wiring for its token-trust plugin — not needed for the openid path (see "What the openid plugin needs") |

The upstream chart was **not vendored** into the Scout repo in the POC — the
deploy pointed `helm install` at an external checkout via `$XNAT_CHART`. This is
the first thing a gitops-friendly setup should change (see "Toward Ansible
automation").

## How the upstream chart lets us extend XNAT

The community chart already exposes every seam the deploy needs. Understanding
these four mechanisms is the key to deploying plugins and config without forking
the chart. All are driven from the XNAT Helm values.

### 1. `extraConfig` → `xnat-conf.properties`

The chart renders a `xnat-conf.properties` Secret (`templates/secret.yaml`) and
appends every key/value under `.Values.extraConfig` to it:

```yaml
{{- with .Values.extraConfig }}
{{- range $key, $value := . }}
    {{ $key }}={{ $value }}
{{- end }}
{{- end }}
```

This is **the** channel for plugin configuration. Any plugin that reads XNAT
config via Spring `@Value("${...}")` or XNAT's preferences mechanism is
configured here. The Scout auth plugin's `scout.keycloak.*` properties ride this
path; the openid plugin's `provider.implementation.*` settings would too.

### 2. `initContainers` → deliver plugin JARs into the plugins volume

The chart shares an `emptyDir` named `home-plugins` mounted at
`/data/xnat/home/plugins`, and splices any user-supplied `.Values.initContainers`
into the init sequence (after `wait-for-postgres`, before the chart's
`home-init`). An init container that writes a JAR into that volume installs a
plugin. Two variants seen in the POC history:

- **From a Secret (current, air-gap friendly):** a `busybox` `cp` from a
  mounted Secret volume into the plugins dir. No network egress at deploy time.
- **From a URL (original POC):** a `curlimages/curl` container that downloads
  the JAR from a release URL (e.g. Bitbucket) into the plugins dir. Simpler, but
  needs egress and pins availability to an external host — not air-gap friendly.

### 3. `plugins` → chart-native image-based plugin installs

The chart also has a first-class `.Values.plugins` map: each entry becomes an
init container built from `{repository}:{tag}` that mounts `home-plugins`. This
is the chart's intended way to ship plugins baked into images. The POC did not
use it (it used hand-rolled `initContainers` instead), but it is the most
gitops-native option if plugin JARs are published as thin images.

### 4. `authplugins` → mount provider properties for auth plugins

For auth-provider plugins specifically, the chart has a dedicated mechanism. For
each entry in `.Values.authplugins` with `auth: true`, it mounts a Secret named
`xnat-plugin-<entryName>` (key `<provider>-provider-properties`) at
`/data/xnat/home/config/auth/<provider>-provider.properties`:

```yaml
authplugins:
  keycloak:           # entryName → Secret xnat-plugin-keycloak
    provider: keycloak  # → mounts key `keycloak-provider-properties`
    auth: true          #   at config/auth/keycloak-provider.properties
```

This is how the **off-the-shelf `xnat-openid-auth-plugin`** is meant to be
configured — see "Plugin example 2" below. The Scout auth plugin does **not**
use this mechanism (it reads everything from `extraConfig`).

## Scout's modifications on top of the chart

The chart is used **as-is, unmodified**; all Scout-specific behavior is
expressed through values and out-of-band Secrets. The notable adjustments,
worth preserving in any future automation:

### Values overrides (`xnat/xnat-values.yaml`)

- **Single-node dev posture:** `replicaCount: 1`, `redis.enabled: false`,
  `activemq.broker.enabled: false`. Fine for one replica; revisit before HA.
- **CNPG-managed Postgres:** `cnpg.cluster.enabled: true` with one instance on
  `local-path`. XNAT's DB is a CloudNativePG cluster, matching the rest of Scout.
- **All storage on `local-path`:** the five XNAT volumes (`xnatdata`, `build`,
  `cache`, `archive`, `prearchive`) plus the CNPG volume. This pins XNAT to
  whichever node first binds the PVCs — single-node-pinned, acceptable for dev.
- **Startup probe widened:** `probes.startup.failureThreshold: 60` (10-minute
  budget) to cover Hibernate's first-boot DDL (3–5 min). Pairs with
  `helm install --wait --timeout 20m`.
- **Traefik + oauth2-proxy ingress:** the ingress wears the shared
  `kube-system-oauth2-proxy-auth` and `-error` middleware annotations — the same
  shape Jupyter / Grafana / Superset / Launchpad use. This is what puts XNAT
  behind Scout SSO at the edge.

### Chart-bug workarounds (these are real footguns to carry forward)

1. **`cnpg.external.postgresqlPort: 5432`** — works around a whitespace bug in
   the chart's `templates/_postgresql.tpl`: the `else` branch of `postgresqlPort`
   emits `"5432"` with no whitespace trimmer, breaking the rendered YAML block
   scalar. Setting the value explicitly forces the clean `if` branch. *(Candidate
   for an upstream fix.)*
2. **`postfix-password` placeholder Secret** — the chart pulls in the
   `bokysan/postfix` mail subchart **unconditionally** and references a
   hardcoded `postfix-password` Secret. Even with `smtpEnabled=false`, the
   `xnat-mail-0` pod sits in `CreateContainerConfigError` (and `--wait` never
   returns) unless the Secret exists. *(Candidate for an upstream
   `condition:` on the mail subchart.)*

### Out-of-band Secrets the values reference

The values file assumes three Secrets exist before `helm install` (for the
auth-plugin deploy):

| Secret | Key | Consumed by | Purpose |
| --- | --- | --- | --- |
| `scout-xnat-auth-plugin-jar` | `plugin.jar` | `install-scout-auth-plugin` init container | The plugin JAR |
| `xnat-prefs-init` | `prefs-init.ini` | mounted at `config/prefs-init.ini` | Skip the setup wizard |
| `postfix-password` | `username`,`password` | mail subchart | Placeholder (unused) |

These hand-created Secrets are the least gitops-friendly part of the POC and the
main thing the automation section addresses.

## Deploying XNAT itself

The end-to-end deploy, as run manually in the POC (substitute your own ingress
hostname for `xnat.dev03.tag.rcif.io` throughout). Two paths exist below — pick
the plugin you're deploying. The conventions:

```bash
export KUBECONFIG=/path/to/your/cluster/kubeconfig
export XNAT_CHART=~/repos/helm-charts/helm/xnat       # chart appVersion 1.9.2.1
```

### 0. Prerequisites

- `helm` and `kubectl` installed; `kubectl get nodes` resolves.
- CloudNativePG operator running (`-n scout-operators get pods | grep cnpg`).
- Traefik running, with the shared oauth2-proxy middlewares present:
  `kubectl -n kube-system get middleware | grep oauth2-proxy` (expect
  `oauth2-proxy-auth` and `oauth2-proxy-error`). oauth2-proxy fronts XNAT under
  **both** plugin models — it is the edge approval gate regardless of which
  plugin establishes the in-app session.
- DNS for the ingress host resolves to the cluster ingress IP.
- Auth substrate deployed (`make install-auth`) — see "What the openid plugin
  needs from the auth substrate" for exactly what that must provision.

### 1. Build the plugin JAR (if building one)

For the Scout plugin, see "Plugin example 1" for the JDK-8 build. For an
off-the-shelf JAR you download instead of build, skip to step 2.

### 2. Helm chart prep

- `cd "$XNAT_CHART"` and confirm a clean tree on `appVersion: 1.9.2.1`.
- Pull subchart deps (Bitnami redis + bokysan postfix):
  ```bash
  helm dependency update
  ```

### 3. Namespace and supporting Secrets

```bash
kubectl create namespace xnat
```

The values file references up to **three** Secrets that must exist before
`helm install`:

- **Plugin JAR Secret** (only for the Secret-mounted delivery, Pattern A). The
  key **must** be `plugin.jar`:
  ```bash
  kubectl -n xnat create secret generic scout-xnat-auth-plugin-jar \
    --from-file=plugin.jar=scout-xnat-auth-plugin/build/libs/scout-xnat-auth-plugin-0.1.0-SNAPSHOT-xpl.jar
  ```
- **First-boot config Secret** — mounted at `config/prefs-init.ini`; XNAT
  self-completes the setup wizard from it on first launch:
  ```bash
  kubectl -n xnat create secret generic xnat-prefs-init \
    --from-file=prefs-init.ini=xnat/prefs-init.ini
  ```
- **Postfix placeholder Secret** — the chart pulls in the `bokysan/postfix` mail
  subchart unconditionally and references a hardcoded `postfix-password` Secret.
  XNAT runs `smtpEnabled=false` so the contents are unused, but without the
  Secret the `xnat-mail-0` pod sits in `CreateContainerConfigError` and
  `helm install --wait` never returns:
  ```bash
  kubectl -n xnat create secret generic postfix-password \
    --from-literal=username=xnat --from-literal=password=unused-dev
  ```

For the `xnat-openid-auth-plugin` path, also create the provider-properties
Secret the `authplugins` mechanism mounts (see "Plugin example 2").

### 4. Helm install

```bash
helm install xnat "$XNAT_CHART" \
  --namespace xnat \
  --values xnat/xnat-values.yaml \
  --wait --timeout 20m
```

The 20-minute timeout pairs with `probes.startup.failureThreshold: 60` in the
values to cover Hibernate's first-boot DDL (3–5 min on first launch).

### 5. Verification

Init containers run in order: `wait-for-postgres` → *(your plugin-install init
container)* → chart's `home-init` → `xnat`.

```bash
kubectl -n xnat get pods -w
kubectl -n xnat describe pod xnat-0 | grep -A4 "Init Containers"
kubectl -n xnat get cluster        # CNPG cluster healthy
```

Confirm the plugin loaded. XNAT's logback only routes ERROR-level logs to
stdout, so the plugin scan is invisible in logs — query the API instead. Query
it **from inside the xnat pod**, not via the ingress: the ingress is fronted by
oauth2-proxy, which intercepts an unauthenticated request and returns the Scout
sign-in page (HTTP 401) instead of reaching XNAT. Hitting `localhost:8080` from
within the pod bypasses Traefik/oauth2-proxy, so the `admin:admin` local account
authenticates against XNAT directly:

```bash
kubectl -n xnat exec sts/xnat -c xnat -- \
  curl -su admin:admin http://localhost:8080/xapi/plugins \
  | grep '"<your-plugin-id>"'      # Scout plugin registers as `xnatScoutAuthPlugin`
```

If `xnat-0` restarts during first boot, watch the DDL bootstrap with
`kubectl -n xnat logs sts/xnat -c xnat --tail=200`.

Then browse the ingress host. Because `prefs-init.ini` was mounted, the setup
wizard is already complete. The local `admin`/`admin` account still exists —
change its password before opening access.

### 6. Teardown (destructive — confirm the cluster first)

```bash
helm uninstall xnat -n xnat
kubectl -n xnat delete cluster --all     # CNPG cluster
kubectl -n xnat delete pvc --all         # local-path PVs — irreversible data loss
kubectl delete namespace xnat
```

### XNAT site configuration (`prefs-init.ini`)

XNAT self-completes its first-boot setup wizard from a `prefs-init.ini` mounted
at `/data/xnat/home/config/prefs-init.ini`. This makes the deploy
non-interactive and reproducible. The POC's file:

```ini
[siteConfig]
siteUrl=https://xnat.dev03.tag.rcif.io
siteId=scout-xnat-dev03
adminEmail=admin@dev03.tag.rcif.io
smtpEnabled=false
initialized=true
requireLogin=true
# Enable the openid plugin's provider on the login page. Without this the page
# shows no SSO link (the list defaults to ['localdb']). Drop 'localdb' to leave
# only the "Sign in with Scout" button — admin still works via the REST API.
enabledProviders=['keycloak']
```

`initialized=true` is what suppresses the wizard. The `admin`/`admin` local
account still exists after first boot — change its password before opening
access. `enabledProviders` is what actually surfaces the openid provider on the
login page (see "Plugin example 2" — this was the original login blocker). Any
XNAT site preference can be seeded here; this is the right place for declarative
XNAT config in a future role. **Caveat:** `prefs-init.ini` only seeds on *first
boot* — on an already-initialized instance, change site preferences via the
`/xapi/siteConfig` API (or the admin UI) instead.

## Deploying plugins (the general pattern)

A plugin deploy is two independent concerns: **getting the JAR into the plugins
dir** and **giving the plugin its configuration**. Pick a delivery mechanism
(§"How the chart lets us extend XNAT") and a config channel, per plugin.

### Delivering the JAR

**Pattern A — Secret + busybox cp (used by the POC, air-gap friendly):**

```yaml
# values: an init container that copies from a mounted Secret volume
initContainers:
  - name: install-<plugin>
    image: busybox:1.36
    command: ['sh', '-c']
    args:
      - >
        set -e;
        cp /mnt/<plugin>/plugin.jar /data/xnat/home/plugins/<plugin>.jar;
        ls -l /data/xnat/home/plugins
    volumeMounts:
      - name: home-plugins
        mountPath: /data/xnat/home/plugins
      - name: <plugin>-jar
        mountPath: /mnt/<plugin>
        readOnly: true
extraVolumes:
  - name: <plugin>-jar
    secret:
      secretName: <plugin>-jar      # key must be `plugin.jar`
```

```bash
kubectl -n xnat create secret generic <plugin>-jar \
  --from-file=plugin.jar=path/to/<plugin>-xpl.jar
```

**Pattern B — download by URL (original POC, simplest, needs egress):**

```yaml
initContainers:
  - name: install-<plugin>
    image: curlimages/curl:8.10.1
    command: ['sh', '-c']
    args:
      - >
        set -e;
        curl -fsSL -o /data/xnat/home/plugins/<plugin>.jar
        https://.../<plugin>-1.4.1-xpl.jar
    volumeMounts:
      - name: home-plugins
        mountPath: /data/xnat/home/plugins
```

**Pattern C — chart-native `plugins`, image-based:** add an entry under
`.Values.plugins` pointing at a thin image that carries the JAR. Requires a
build/publish step to wrap the JAR in an image and push it to Harbor.

**Pattern D — Maven/Gradle coordinates resolved through Nexus:** an init
container that fetches the JAR by its published coordinates from the Scout Nexus
Maven proxy (ADR 0017), rather than from a raw URL. This is the natural fit for
plugins that are published to a Maven repository (many XNAT plugins, including
`xnat-openid-auth-plugin`, are):

```yaml
initContainers:
  - name: install-<plugin>
    image: maven:3.9-eclipse-temurin-8
    command: ['sh', '-c']
    args:
      - >
        set -e;
        mvn -q org.apache.maven.plugins:maven-dependency-plugin:3.6.1:copy
        -Dartifact=org.nrg.xnatx.plugins:openid-auth-plugin:1.4.1:xpl:jar
        -DremoteRepositories={{ maven_proxy_url }}
        -DoutputDirectory=/data/xnat/home/plugins;
        ls -l /data/xnat/home/plugins
    volumeMounts:
      - name: home-plugins
        mountPath: /data/xnat/home/plugins
```

The coordinate (`group:artifact:version:classifier:type`) is declarative and
version-pinned in git, and resolution goes through the **same controlled
pull-through proxy as conda/PyPI/RPM** — so unlike Pattern B it needs **no
arbitrary internet egress and works on air-gapped clusters**. (`maven_proxy_url`
above is illustrative: Nexus can proxy Maven, but Scout doesn't define a Maven
proxy var yet — it would be a new per-format proxy URL following the existing
`pip_proxy_url` / `conda_proxy_url` convention from ADR 0017.) Use a released
version, not a `-SNAPSHOT` (snapshots are mutable, which breaks reproducibility);
pin a checksum if the resolver supports it. Since the chart has no native
coordinate support yet, this is currently a hand-rolled `initContainers` entry —
making it first-class is upstream improvement #1 below.

Multiple plugins are just multiple init containers / `plugins` entries; nothing
limits a deploy to one plugin.

### Which delivery pattern? (the gitops view)

"GitOps-friendly" here means: the deployed JAR is **declared in version-controlled
config** (not created by an out-of-band step), **pinned/reproducible** (a given
git revision always yields the same artifact), and pulled through Scout's
**controlled registries/proxies** (no ad-hoc external dependency). By that bar:

| Pattern | GitOps verdict | Why |
| --- | --- | --- |
| **A** — Secret + cp | Weakest | The JAR *content* lives in a hand-created Secret (`kubectl create secret`), not in git; not reproducible from a git revision. Fine for a quick deploy or a locally-built dev JAR, but it's the out-of-band state gitops tries to eliminate. |
| **B** — URL | Fragile | The URL is declarative and in git, but typically unpinned (content behind a URL can change/vanish), needs egress, and depends on an external host. |
| **C** — image | **Strongest** | The JAR is baked into a container image referenced by `repository:tag` (ideally `@sha256:` digest). Images are the *native unit of k8s/gitops*: immutable, digest-addressable, pulled through Harbor like every other Scout image, cached and scannable, and the reconciler treats the image ref as declarative desired state. Cost: a build/publish step to produce the thin image. |
| **D** — coordinates via Nexus | **Strong** | Coordinate is declarative, version-pinned, in git, and resolved through the controlled Nexus proxy (works air-gapped). Consumes the plugin in its *natural published form* — no image to build. Slightly behind C because resolution happens at deploy time against Nexus (a runtime dependency) and a bare coordinate isn't digest-immutable unless you also pin a checksum. |

In short: **C and D are both good gitops options** — C when you're willing to
wrap the JAR in an image, D when the plugin is already published to a Maven repo
(the common case for off-the-shelf XNAT plugins). D is strictly better than B for
air-gapped clusters. A is the fallback for locally-built or unpublished JARs.

### Configuring a plugin

- **Via `extraConfig`** (Spring `@Value` / XNAT prefs): add keys under
  `extraConfig`; they land in `xnat-conf.properties`. This is how
  `scout-xnat-auth-plugin` is configured.
- **Via `authplugins`** (auth-provider properties files): add an entry under
  `authplugins` and supply the matching `xnat-plugin-<name>` Secret. This is how
  `xnat-openid-auth-plugin` is configured.

## Plugin example 1: `scout-xnat-auth-plugin` (the POC's custom plugin)

A Scout-authored XNAT plugin that wires XNAT into Scout's token-only auth
posture. Source and tests are in-tree at `scout-xnat-auth-plugin/`.

**Build** (must be JDK 8 for the 1.9.2 / Gradle-8.x baseline):
```bash
cd scout-xnat-auth-plugin
export JAVA_HOME=$(/usr/libexec/java_home -v 1.8)
./gradlew --stop                  # drop any daemon left running under a newer JDK
./gradlew xnatPluginJar
# → build/libs/scout-xnat-auth-plugin-0.1.0-SNAPSHOT-xpl.jar
```
The `-xpl.jar` is the shadowed artifact that bundles `nimbus-jose-jwt` so XNAT
can validate the access token at runtime. Gradle is pinned to 8.x (runs on Java
8–24), but the build itself targets XNAT 1.9.2 / JDK 8.

> **Build under JDK 8.** Gradle has no toolchain pin, so it uses whatever
> `JAVA_HOME`/daemon is current. Building under a newer JDK (17/21/22) fails with
> `aspectjweaver-*.jar: Invalid CEN header (invalid zip64 extra data field size)`
> and ~40 cascading "cannot access org.nrg.*" compile errors — the newer JDK's
> strict zip validation rejects a dependency jar. Point `JAVA_HOME` at a JDK 8
> install and stop any stale daemon (both shown above) before building.

**Delivery:** Pattern A (Secret `scout-xnat-auth-plugin-jar` + busybox cp).

**Configuration** (via `extraConfig`):

| Property | POC value | Purpose |
| --- | --- | --- |
| `scout.keycloak.issuer` | `https://keycloak.<host>/realms/scout` | JWT issuer — **must be the public hostname** (Keycloak stamps the frontend URL as `iss`) |
| `scout.keycloak.jwks_uri` | `http://keycloak-service.scout-core.svc.cluster.local:8080/.../certs` | JWKS endpoint (in-cluster URL is fine; signing keys are universal) |
| `scout.keycloak.client_id` | `xnat` | Expected `aud` on bearer tokens; client whose roles are read |
| `scout.keycloak.required_role` | `xnat-access` | Required role on the validated identity |
| `scout.keycloak.oauth2_proxy_client_id` | `oauth2-proxy` | `azp` the browser-path access token must carry |
| `scout.username_prefix` | `keycloak` | XNAT username = `<prefix>-<sub>` |
| `scout.headers.access_token_header` | `X-Auth-Request-Access-Token` | The header oauth2-proxy forwards (sole browser-path identity source) |

Design notes worth carrying forward:
- **No client secret.** The `xnat` Keycloak client is bearer-only (an audience
  target with no secret); the plugin validates tokens locally against JWKS.
  There is no value to keep in sync with the inventory.
- **Two paths:** `HeaderTrustFilter` (browser, via oauth2-proxy's forwarded
  access token) and `BearerTokenFilter` (in-cluster API callers presenting a
  Keycloak bearer with `aud=xnat`). Both provision a `keycloak-<sub>` XNAT user
  on first sight. No server-side token exchange.

## Plugin example 2: `xnat-openid-auth-plugin` (off-the-shelf — to return to)

The earlier `xnat-dev-wt` exploration used the community
`xnat-openid-auth-plugin` (`xnatx/openid-auth-plugin`, v1.4.1) instead of a
custom plugin. This is the plugin Scout **has now returned to** — it was deployed
and verified on the live 1.10.0 cluster (see "XNAT 1.10 / JDK 21 migration"). The
configuration is recorded here.

> **Use 1.5.0 for XNAT 1.10.** Pair the plugin's parent version with the XNAT
> version: **1.5.0** is built against `org.nrg:parent` 1.10.0, **1.4.1** against
> 1.9.2. For the 1.10 deploy the 1.5.0 JAR was built from source
> (`~/repos/openid-auth-plugin`, `./gradlew xnatPluginJar`) **under JDK 21**.

**Delivery:** the URL form below is Pattern B — download the `-xpl.jar` from the
upstream release URL:
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
The live 1.10.0 deploy actually used **Pattern A** (build locally, deliver via a
Secret) because the dev03 cluster can't resolve `bitbucket.org` (NXDOMAIN), so
Pattern B is unavailable there. For an air-gapped or gitops-friendly setup,
prefer **Pattern D** — resolve it by its Maven coordinates through the Nexus
proxy (it's a published XNAT plugin, so no image to build and no arbitrary
egress) — or Pattern C (republish as an image into Harbor). Pattern A remains the
fallback for a locally-built JAR.

**Configuration:** unlike the Scout plugin, the openid plugin reads a provider
**properties file** from `config/auth/` — the chart's `authplugins` mechanism
mounts it. The `xnat-dev-wt` values declared:

```yaml
authplugins:
  keycloak:
    provider: keycloak
    auth: true
```

…which the chart mounts as `config/auth/keycloak-provider.properties` from a
Secret `xnat-plugin-keycloak` (key `keycloak-provider-properties`).

The provider properties content used on `xnat-dev-wt` (`xnat/openid-provider.properties`):

```properties
auth.method=openid
type=openid
provider.id=keycloak
name=Scout SSO
auto.enabled=true
auto.verified=true

siteUrl=https://xnat.dev03.tag.rcif.io
preEstablishedRedirUri=/openid-login

openid.keycloak.clientId=xnat
openid.keycloak.clientSecret=<keycloak xnat client secret>
openid.keycloak.accessTokenUri=https://keycloak.<host>/realms/scout/protocol/openid-connect/token
openid.keycloak.userAuthUri=https://keycloak.<host>/realms/scout/protocol/openid-connect/auth
openid.keycloak.userInfoUri=https://keycloak.<host>/realms/scout/protocol/openid-connect/userinfo
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

Things to reconcile when returning to this plugin (all three are now **verified**
against the live 1.10.0 + plugin-1.5.0 deploy, with a full browser login
round-trip, 2026-06-04):

- **Filename mismatch — resolved by `provider: openid`.** The chart's
  `authplugins` mechanism mounts the file as `<provider>-provider.properties`,
  and the plugin expects `openid-provider.properties`. Setting `provider: openid`
  on the `authplugins` entry (entry key still `keycloak`) makes the chart mount
  it as `openid-provider.properties` from Secret `xnat-plugin-keycloak` (key
  `openid-provider-properties`), with `provider.id=keycloak` *inside* the file.
  **Verified:** with that config the plugin reads the file and registers the
  provider. (Mounting via a plain `extraVolumes`/`extraVolumeMounts` pair is an
  equivalent fallback but wasn't needed.)
- **The `enabledProviders` site setting *is* required — this was the login
  blocker.** Dropping a `*-provider.properties` file into `config/auth/` makes
  XNAT *discover* the provider and stand up its `/openid-login` endpoint, but the
  login page will **not** show a "Sign in with Scout" link until the provider's
  id is in the site config `enabledProviders` list. That list defaults to
  `['localdb']` (`SiteConfigPreferences.getEnabledProviders()`), and
  `XnatProviderManager.getLinkedEnabledProviders()` filters the rendered links to
  only enabled provider ids — so an enabled-but-absent provider is invisible.
  Add the provider id (`keycloak`, from `provider.id` in the properties file):
  ```ini
  # prefs-init.ini, [siteConfig]  (single-quoted JSON; matches XNAT's own default)
  enabledProviders=['keycloak']
  ```
  Dropping `localdb` from the list (rather than `['localdb','keycloak']`) removes
  the username/password form so the page shows **only** the SSO button; the
  built-in `admin`/`admin` account still authenticates via the REST API. On an
  already-initialized instance (where `prefs-init.ini` no longer re-applies), set
  it through the API instead:
  ```bash
  kubectl -n xnat exec sts/xnat -c xnat -- curl -s -u admin:admin \
    -X POST -H 'Content-Type: application/json' \
    http://localhost:8080/xapi/siteConfig -d '{"enabledProviders":["keycloak"]}'
  ```
  **Verified:** with `keycloak` enabled, the login page renders the
  "Sign in with Scout" link, and clicking it completes the full OIDC round-trip —
  Keycloak authenticates (silently via SSO when a session already exists), the
  `/openid-login?code=…` callback exchanges the code, and XNAT provisions and
  logs in a `keycloak-<sub>` user, landing on the main dashboard.
- **This plugin makes XNAT its own OIDC client** (app-as-OIDC-client), not
  token-trust based: it runs its own authorization-code flow via
  `preEstablishedRedirUri=/openid-login` and needs a **real client secret**, so
  the `xnat` Keycloak client must be a confidential client with a secret and
  redirect URIs — *different* from the bearer-only audience target the Scout
  plugin uses. The full confidential-client config is recovered in "The
  full-featured `xnat` Keycloak client" below.

  **This plugin can and should still live behind oauth2-proxy** — it does *not*
  require opening XNAT's ingress. This is exactly the model every other Scout
  service uses: Grafana (`/login/generic_oauth`), Superset
  (`/oauth-authorized/keycloak`), JupyterHub (`/hub/oauth_callback`), Temporal,
  Open WebUI, etc. all sit behind oauth2-proxy *and* are their own Keycloak OIDC
  client with their own callback path. `/openid-login` is XNAT's app-level
  callback, the direct analogue of those — **not** a public bypass. It stays
  behind the oauth2-proxy ForwardAuth middleware and is reached only after a user
  has already passed the edge approval gate.

  The two layers do distinct jobs and coexist:
  - **oauth2-proxy** = the *approval gate* ("is this an approved `scout-user`?"),
    enforced at Traefik. It does not log the user into XNAT.
  - **the openid plugin** = XNAT's *own session/identity* (a real XNAT user with
    XNAT roles), established by its own OIDC flow.

  Sequence: browser → oauth2-proxy authenticates + checks `scout-user` → request
  reaches XNAT → the plugin redirects to Keycloak, which completes **silently via
  SSO** (Keycloak already has a session) → the `/openid-login?code=…` callback
  passes back through oauth2-proxy (the user is already authenticated) → XNAT
  exchanges the code and establishes its session. The only cost over the
  header-trust plugin is that second, silent OIDC hop — identical to what every
  other Scout OIDC service already incurs. (Contrast the POC's
  `scout-xnat-auth-plugin`, which is header-trust: it reads oauth2-proxy's
  forwarded token instead of running its own flow, so it needs no XNAT-side
  client or callback — the minority model among Scout services.)

## Auth substrate the POC built for `scout-xnat-auth-plugin` (historical)

> **This whole section is historical context, not a prerequisite list.** The
> Ansible-role edits below were developed in the POC **specifically for the
> token-trust `scout-xnat-auth-plugin`** (header forwarding, in-cluster bearer
> tokens). They are **not present on `main`**, and **most are not needed for the
> openid-plugin path** the implementation is taking. What the openid plugin
> actually requires from the auth substrate is short — see "What the openid
> plugin needs" below. The POC edits are recorded only so their intent is legible
> if you go spelunking in the POC branch.

POC edits, and whether the openid-plugin path needs them:

- **Keycloak realm (`scout-realm.json.j2`)** — *partly needed, in adjusted form.*
  The POC ended with a bearer-only `xnat` client (`bearerOnly: true`), an
  `xnat-access` client role composited onto the `scout-user` group, and an
  `xnat-audience` `oidc-audience-mapper` on the `jupyterhub` client. For the
  openid plugin the client must instead be **confidential** (see "The
  full-featured `xnat` Keycloak client" below), and the **`jupyterhub`
  audience mapper is not needed** (it existed only to let notebook bearer tokens
  carry `aud=xnat`).
- **oauth2-proxy (`values.yaml.j2`): `set_xauthrequest = true` +
  `pass_access_token = true` + the `X-Auth-Request-Access-Token`
  `authResponseHeaders` entry** — *NOT needed for the openid plugin.* These made
  oauth2-proxy forward the Keycloak access token to XNAT so the Scout plugin's
  `HeaderTrustFilter` could trust it. The openid plugin does its **own OAuth
  login flow with Keycloak**, so XNAT never reads a forwarded token — leave
  oauth2-proxy at its stock config. (oauth2-proxy still fronts XNAT as the edge
  approval gate; that's just the ingress middleware annotation in the values,
  not these header flags.)
- **Jupyter (`values.yaml.j2`): NetworkPolicy egress to `xnat_namespace:8080`
  + `admin:auth_state!user` / `refresh_pre_spawn: true`** — *NOT needed in the
  first pass.* These supported notebooks calling XNAT in-cluster with a
  per-user Keycloak token (`scout_xnat` / scout-xnatpy). No notebook token-based
  login is in the first pass, so no Jupyter egress or token changes are required.
  (Revisit only if/when programmatic notebook→XNAT access is added.)
- **Defaults / inventory (`scout_common/defaults/main.yaml`,
  `inventory.example.yaml`): `xnat_namespace` and `keycloak_xnat_client_id`** —
  *still useful.* Plain naming defaults, independent of the auth model.
  `xnat_namespace` is independent of the six consolidated `scout_*_namespace`
  variables — XNAT runs in its own namespace.

### What the openid plugin needs from the auth substrate

Reduced to essentials, the openid-plugin path needs only:

1. A **confidential `xnat` Keycloak client** with a secret and the `/openid-login`
   redirect URI (next section), holding the `xnat-access` role mapped to
   `scout-user`. This is the authorization gate.
2. **oauth2-proxy fronting XNAT's ingress** as the edge approval gate — the same
   ForwardAuth middleware annotation every other Scout service uses, with **no**
   token-forwarding flags.
3. Naming defaults (`xnat_namespace`, `keycloak_xnat_client_id`).

No oauth2-proxy header forwarding, no `jupyterhub` audience mapper, and no
Jupyter egress/token changes.

One thing this needs *on the XNAT side* (not the auth substrate, but easy to
forget alongside it): the provider id must be added to XNAT's `enabledProviders`
site setting, or the login page shows no SSO link. See "Plugin example 2" — this
was the login blocker on the first 1.10 deploy.

## The full-featured `xnat` Keycloak client (to re-enable)

The POC's realm template ended on a `bearerOnly: true` client — a **late
adjustment** made when the Scout plugin dropped server-side token exchange and
moved to local audience validation. Before that, the POC realm carried a full
confidential client. That fuller client is the configuration to provision when
XNAT is actually deployed (and especially for the OIDC-flow
`xnat-openid-auth-plugin`, which needs a real secret and redirect URIs). The POC
form, to add to `scout-realm.json.j2`'s `clients` list:

```jsonc
{
  "name": "{{ keycloak_xnat_client_id }}",
  "description": "XNAT Client",
  "enabled": true,
  "clientId": "{{ keycloak_xnat_client_id }}",
  "clientAuthenticatorType": "client-secret",
  "secret": "{{ keycloak_xnat_client_secret }}",
  "redirectUris": [
    "https://xnat.{{ server_hostname }}/oauth2/callback"
  ],
  "webOrigins": [
    "https://xnat.{{ server_hostname }}"
  ],
  "attributes": {
    "pkce.code.challenge.method": "S256",
    "standard.token.exchange.enabled": "true"
  },
  "protocolMappers": [
    {
      "name": "groups",
      "protocol": "openid-connect",
      "protocolMapper": "oidc-group-membership-mapper",
      "consentRequired": false,
      "config": {
        "claim.name": "groups",
        "full.path": "false",
        "id.token.claim": "true",
        "access.token.claim": "true",
        "userinfo.token.claim": "true"
      }
    }
  ],
  "defaultClientScopes": [
    "web-origins", "acr", "profile", "roles", "basic", "email", "microprofile-jwt"
  ],
  "optionalClientScopes": [
    "address", "phone", "organization", "offline_access"
  ]
}
```

Adjustments when re-enabling it:

- **Add the secret.** The full client is confidential, so a
  `keycloak_xnat_client_secret` must be declared in the inventory. The POC's
  `inventory.example.yaml` declaration was:
  ```yaml
  keycloak_xnat_client_secret: $(openssl rand -hex 16 | ansible-vault encrypt_string --vault-password-file vault/pwd.sh)
  ```
  Note: this is **required even if XNAT isn't deployed yet**, because the realm
  always provisions the client (the Keycloak install fails without the secret).
  Add a matching `docs/source/technical/inventory.md` entry too.
- **`redirectUris` is the app's own callback, behind oauth2-proxy.** For the
  `xnat-openid-auth-plugin` set it to `https://xnat.{{ server_hostname }}/openid-login`
  (matching `preEstablishedRedirUri=/openid-login` in the provider properties).
  This is XNAT's app-level OIDC callback — the analogue of Grafana's
  `/login/generic_oauth` or Superset's `/oauth-authorized/keycloak` — and it
  lives **behind** the oauth2-proxy ForwardAuth middleware, not on an open path.
  oauth2-proxy still fronts XNAT; this redirect URI does not change that. (The
  `/oauth2/callback` value carried in the recovered POC block above was
  oauth2-proxy's *own* callback, used when the Scout token-trust plugin fronted
  the browser path; for the openid plugin, replace it with `/openid-login`.)
- **`standard.token.exchange.enabled` is optional now.** It supported the
  server-side STX V2 flow the Scout plugin used to do. The openid plugin doesn't
  need it; drop it unless a token-exchange consumer returns.
- **Keep the `xnat-access` client role and its `scout-user` default-role
  mapping** (separate blocks in the template, unchanged by the bearer-only
  refactor) — that's the authorization gate both plugins rely on.
- **The `jupyterhub` `xnat-audience` mapper is *not* needed initially.** Per the
  current direction we only want the `xnat` client itself re-enabled; the
  audience mapper on the Jupyter client (which stamps `aud=xnat` for the
  in-cluster bearer/API path) can stay deferred until that path is wanted. The
  browser login path through the openid plugin doesn't depend on it.

## Toward Ansible automation (the eventual goal)

> **Status: largely implemented.** The `xnat` Ansible role
> (`ansible/roles/xnat/`, `make install-xnat`, gated by `enable_xnat`) now folds
> the manual POC steps into declarative Ansible. Notable deltas from the design
> sketch below, decided during implementation:
> - **No wrapper chart.** The role consumes the upstream chart directly and keeps
>   all customization in templated values + Ansible-created Secrets (the chart
>   owns no Secrets). Plugin installation uses the chart's existing
>   `initContainers` value seam — no chart change needed.
> - **Chart sourcing:** the chart is unpublished, so the role clones
>   `NrgXnat/helm-charts` at a pinned tag on the jump node and `helm dependency
>   update`s it. Publishing it upstream is a parallel, non-blocking effort.
> - **Plugin installer:** `docker/xnat-plugin-installer/` is a source-agnostic
>   init container (file/url/coordinates) that also performs the logback→stdout
>   rewrite (improvement #1 below, realized Scout-side rather than upstream).
> - **postgresqlPort bug** is already fixed upstream (≥ chart 1.0.2) — no workaround.
> - **Postfix:** disabled in intent (XNAT routes at Scout's shared relay); the
>   placeholder Secret remains only until the upstream `condition: mail.enabled`
>   change (improvement #2) lands.
> - **openid coordinate:** `au.edu.qcif.xnat.openid:openid-auth-plugin:1.5.0:xpl`
>   at NrgXnat Artifactory, proxied via a new Nexus maven group (`maven_proxy_url`).
> - **Keycloak client** is now gated on `enable_xnat`.
>
> The original design discussion is preserved below for rationale.

A future `xnat` role (mirroring the existing per-component roles) should fold the
manual POC steps into declarative, idempotent Ansible. The gitops-friendly
target — *anything that can be a chart, is a chart*:

1. **Vendor or wrap the chart.** Stop depending on an external `helm-charts`
   checkout. Either (a) add the XNAT chart as a versioned dependency of a small
   **Scout wrapper chart** under `helm/xnat/` (the way `helm/scout-dashboards`,
   `helm/open-webui-bootstrap`, etc. are structured), pinning the upstream
   version and carrying the Scout values as the wrapper's defaults; or
   (b) contribute the two chart-bug workarounds upstream and track a released
   version. A wrapper chart also gives a home for the prefs-init and plugin-config
   templating below.
2. **Template `xnat-values.yaml`** from inventory (`server_hostname`,
   `xnat_namespace`, `keycloak_xnat_client_id`, storage classes/sizes, replica
   count) — exactly as every other role templates its `values.yaml.j2`.
3. **Manage Secrets declaratively.** Replace the three `kubectl create secret`
   steps with `kubernetes.core.k8s` tasks (or chart-managed Secrets):
   - Plugin JAR: prefer Pattern D (Maven coordinates via the Nexus proxy) for
     published plugins, or Pattern C (publish the JAR as an image to Harbor and
     use the chart's `plugins:` map) — both avoid a hand-created binary Secret. If
     staying with a Secret, template it from a built artifact. Better still if the
     flexible upstream installer (improvement #1 below) lands — then a plugin is
     a `pluginInstall:` list entry pointing at coordinates/URL/file/image.
   - `prefs-init.ini`: template from inventory and ship as a chart-managed Secret.
   - `postfix-password`: chart-managed placeholder (or fix the subchart
     condition upstream and delete it).
4. **Make plugins a list.** Model `xnat_plugins` as an inventory list, each with
   a delivery mechanism (image/url/secret) and a config block, so adding a plugin
   is a data change, not a template edit. The single-plugin deploy above is the
   degenerate case.
5. **Plugin config from inventory.** Generate the `extraConfig` block (Scout
   plugin) or the `authplugins` + provider-properties Secret (openid plugin)
   from inventory variables, reusing the Keycloak client IDs/roles already in
   `scout_common` defaults.
6. **Wire into the playbooks/Makefile** as `make install-xnat`, gated like the
   optional features (a `enable_xnat` flag), and sequence it after
   `install-auth`.

Open decision that shapes the role: **which plugin / auth model.** The Scout
token-trust plugin and the off-the-shelf openid plugin imply *different* Keycloak
client shapes (bearer-only audience target vs. confidential OIDC client) and a
*different in-app identity mechanism* (trust oauth2-proxy's forwarded token vs.
XNAT runs its own OIDC flow). **Both sit behind oauth2-proxy** — that does not
change between them; oauth2-proxy remains the edge approval gate either way, and
the openid plugin's `/openid-login` is an app-level callback behind the proxy
(the same posture as Grafana/Superset/Open WebUI), not an open ingress. The POC
realm template ended on the token-trust (bearer-only) shape. Returning to the
openid plugin (the stated direction) means switching the `xnat` client to
confidential and deciding whether to support both models via inventory flags or
to standardize on the openid one.

## Deploying to a dev environment (unpublished image / chart)

In a real deploy the `xnat-plugin-installer` image comes from GHCR (pulled
through Harbor) and the chart from a published repo. In dev — before CI has
published the image, or while iterating on un-published chart edits — you have to
get those artifacts onto the cluster yourself. The role has two override hooks
for exactly this.

### The plugin-installer image

Build it from the repo:

```bash
docker build -t ghcr.io/washu-tag/xnat-plugin-installer:dev docker/xnat-plugin-installer/
```

Then get it onto the cluster by one of:

**A. Single-node / non-air-gapped — import straight into k3s containerd.** This
is what Scout's CI does for locally-built images. On the node that runs XNAT:

```bash
docker save ghcr.io/washu-tag/xnat-plugin-installer:dev -o /tmp/xpi.tar
# copy /tmp/xpi.tar to the node if you built elsewhere, then:
sudo k3s ctr -n k8s.io images import /tmp/xpi.tar
# Pin so kubelet image-GC can't evict a tag that exists in no remote registry:
sudo k3s ctr -n k8s.io images label \
  ghcr.io/washu-tag/xnat-plugin-installer:dev io.cri-containerd.pinned=pinned
```

Point the role at that tag in inventory:

```yaml
xnat_plugin_installer_image_tag: dev
```

The role sets `imagePullPolicy: IfNotPresent` on the installer init container, so
the imported image is used without attempting a pull. (Keep a non-`latest` tag
like `dev` — with `latest`, kubelet's default `Always` policy would still be
overridden by our `IfNotPresent`, but a distinct tag avoids confusion with a
real `latest` in Harbor.)

**B. Staging Harbor — push to your own project.** You don't need (and can't use)
the `ghcr.io`→Harbor pull-through *proxy* projects for an unpublished image.
Instead create your own regular **hosted** Harbor project, push the dev image
into it, and point the inventory at that project directly — the role's
`xnat_plugin_installer_image_repository` override means the deploy pulls from
your project, bypassing the mirror entirely.

1. In Harbor, create a hosted project, e.g. `xnat-dev` (make it public for dev,
   or wire an imagePullSecret — see note).
2. Build, tag, and push:
   ```bash
   docker tag ghcr.io/washu-tag/xnat-plugin-installer:dev \
     <harbor-host>/xnat-dev/xnat-plugin-installer:dev
   docker login <harbor-host> && docker push <harbor-host>/xnat-dev/xnat-plugin-installer:dev
   ```

   > **Cert-trust prerequisite (the fiddly part).** The cluster *nodes* already
   > trust the staging Harbor cert (ADR 0016), but your **local Docker daemon
   > does not** — `login`/`push` will fail with an x509 error until you add the
   > staging server's self-signed CA to the daemon's trust. The catch: that trust
   > lives wherever your daemon runs, which is tooling-specific and differs across
   > a team:
   > - **Native Linux dockerd:** drop the CA at
   >   `/etc/docker/certs.d/<harbor-host>/ca.crt` (use `<harbor-host>:<port>` if
   >   non-443), then `systemctl restart docker`.
   > - **colima / Lima / Docker Desktop / Rancher Desktop:** the daemon runs in a
   >   VM, so the cert must go *inside that VM*, not on your laptop. e.g. for
   >   colima: `colima ssh`, then place the CA under
   >   `/etc/docker/certs.d/<harbor-host>/ca.crt` in the VM and restart docker
   >   there. Each runtime has its own path/procedure — set it up for whatever
   >   you run.
   >
   > Get the CA from the staging node (its Harbor TLS cert) or with
   > `openssl s_client -connect <harbor-host>:443 -showcerts`. Because this is
   > per-developer setup that varies by tooling, pushing to your **own GHCR
   > namespace** (below) is often the lower-friction path — it reuses the existing
   > trusted ghcr→Harbor proxy and needs no local cert wrangling.
3. Point the role at it:
   ```yaml
   xnat_plugin_installer_image_repository: <harbor-host>/xnat-dev/xnat-plugin-installer
   xnat_plugin_installer_image_tag: dev
   ```

A public project is simplest for dev; a private one needs an imagePullSecret for
`<harbor-host>` on the XNAT pod's ServiceAccount. (Pushing to your own GHCR
namespace instead also works — the existing ghcr→Harbor proxy then serves it
unchanged with no inventory override.)

### The Helm chart (and un-published chart edits)

By default the role clones `NrgXnat/helm-charts` at `xnat_chart_git_ref` and runs
`helm dependency update` on the jump node — no publishing needed. To test changes
that aren't on `main`:

- **A branch/fork on a remote:** point the role at it —
  ```yaml
  xnat_chart_git_repo: https://github.com/<you>/helm-charts
  xnat_chart_git_ref: my-mail-condition-branch
  ```
- **A purely local working copy:** set `xnat_chart_path` to your chart directory.
  The role then skips the clone *and the `helm dependency update`*, so resolve
  the subchart deps yourself once:
  ```bash
  helm dependency update ~/repos/helm-charts/helm/xnat
  ```
  ```yaml
  xnat_chart_path: /home/you/repos/helm-charts/helm/xnat
  ```

### Resolving the openid plugin in dev

The default openid plugin is published only to NrgXnat Artifactory. With
`package_proxy_mode: none` (typical dev), `maven_proxy_url` defaults to a comma
list of Maven Central **and** NrgXnat Artifactory, so the installer resolves it
with no extra config (the node needs egress to `nrgxnat.jfrog.io`). With
`package_proxy_mode: nexus`, it resolves through the Nexus maven group instead
(no egress). If your dev cluster can reach neither, deliver the plugin via
`source.type: file` (a locally-built jar Secret) instead.

## Possible upstream chart improvements (brainstorming)

The deploy works against the chart as-is, but several Scout-side workarounds
exist only because the chart doesn't yet cover the case. These are candidates to
contribute upstream — moving them into the chart keeps the Scout side thinner and
more gitops-friendly. None are committed work; this is a wishlist.

### 1. A flexible plugin-install init container (highest value)

Today the chart installs plugins only as **JARs baked into an image** (the
`plugins:` map → an init container per `{repository}:{tag}`). The POC instead
hand-rolled an `initContainers:` entry to `cp` a JAR from a Secret, or `curl` it
from a URL. It would be cleaner to migrate that copy-in init container **back
into the chart** as a first-class, source-agnostic plugin installer — an
alternative to the image-embedded mechanism rather than a replacement.

Proposed shape: each plugin entry names a **source**, and a single chart-provided
init container resolves it into `/data/xnat/home/plugins`:

```yaml
pluginInstall:
  - name: scout-auth
    source:
      secret: { name: scout-xnat-auth-plugin-jar, key: plugin.jar }
  - name: openid
    source:
      url: https://bitbucket.org/xnatx/openid-auth-plugin/downloads/openid-auth-plugin-1.4.1-xpl.jar
  - name: some-plugin
    source:
      # Maven/Gradle coordinates, resolved against a configurable repo
      coordinates: org.nrg.xnat.plugins:some-plugin:1.2.3:xpl@jar
  - name: local-dev
    source:
      file: /mnt/plugins/local-built.jar   # hostPath / pre-mounted volume
```

The init container would accept any of: **a local file / Secret** (Pattern A),
**Maven/Gradle coordinates** resolved from a configurable repository — works
nicely with Scout's air-gapped Nexus proxy, ADR 0017 (Pattern D), or **a URL**
(Pattern B). That folds the three non-image delivery patterns into one
declarative list, complementing the chart's existing image mechanism (Pattern C)
rather than replacing it. *(Mentioned as a future upstream improvement; not
implemented here.)*

### 2. Make the mail subchart conditional

The `bokysan/postfix` subchart installs unconditionally and hard-references a
`postfix-password` Secret, forcing the placeholder-Secret workaround even when
`smtpEnabled=false`. A `condition:` on the dependency (and/or chart-managed
generation of the Secret) would remove the footgun entirely.

### 3. Fix the `postgresqlPort` whitespace bug

`templates/_postgresql.tpl`'s `else` branch emits `"5432"` without a whitespace
trimmer, breaking the rendered YAML block scalar — the reason
`cnpg.external.postgresqlPort` must be set explicitly. A one-line `{{-`/`-}}` fix
upstream removes the need for the workaround.

### 4. First-class first-boot / site-config support

The POC seeds `prefs-init.ini` via a hand-created Secret and an `extraVolumes`
mount. The chart could expose a `siteConfig:` values block that renders the
`prefs-init.ini` Secret and mounts it automatically — making non-interactive,
declarative XNAT site configuration a supported feature rather than a
mount-it-yourself convention.

### 5. Built-in auth-config plumbing without the filename quirk

The `authplugins` mechanism mounts `<provider>-provider.properties`, which
mismatches plugins (like `xnat-openid-auth-plugin`) that expect a fixed filename
(`openid-provider.properties`). Allowing an explicit target filename per entry
(or documenting the convention clearly) would remove the reconciliation step
noted in the openid section.

## XNAT 1.10 / JDK 21 migration (the upstream image is fixed — verified)

The POC originally deployed XNAT **1.9.2.1 on JDK 8** because the first
`xnatworks/xnat-web:1.10.0` image was internally inconsistent. **That image has
since been re-cut and now works.** On 2026-06-04 a live 1.10.0 + OpenID-plugin
deploy was stood up on the dev03 cluster and verified end to end (details below),
so the "blocked upstream" framing this section used to carry is obsolete.

**The build mechanics were always sound.** Comparing `org.nrg:parent` 1.9.2 vs
1.10.0, the dependency delta is narrow — Spring (5.3.39), Spring Security
(5.7.13), embedded Tomcat (9.0.93, still `javax.servlet`), Hibernate, Jackson,
slf4j, logback, aspectj, guava are all **unchanged**. The change with teeth is
the Java target: the 1.10.0 nrg JARs are compiled to **JDK 21 bytecode**
(`--release 21`). So the migration is a build-toolchain bump (JDK 21 toolchain +
1.10.0 BOM for any in-tree plugin) plus the matching runtime image — not an API
rewrite.

**The old blocker (now resolved).** The original `1.10.0` image shipped a
**Temurin JDK 8** JRE (`1.8.0_422`) against **JDK 21 bytecode** nrg JARs. At
boot, Tomcat's classpath scan hit `UnsupportedClassVersionError` per nrg class
and silently skipped them, logging only `No Spring WebApplicationInitializer
types detected on classpath`; XNAT never wired its Spring config and came up
empty (hung on `/xapi/plugins`, 404s on `/`). The re-cut image fixes exactly
this: its JRE is now **Temurin 21** (`21.0.11`) and the bundled nrg classes are
**JDK 21 bytecode** (class file major version 65) — JRE and bytecode level are
consistent.

**Pre-flight check (still worth running on any new tag).** Before a cluster
change, inspect a candidate image directly to catch a JRE/bytecode mismatch in a
minute. Both checks passed on `1.10.0`:

```bash
kubectl run xnat-inspect --rm -i --restart=Never \
  --image=xnatworks/xnat-web:1.10.0 --command -- sh -c '
    java -version;                              # -> Temurin 21.0.11
    f=/usr/local/tomcat/webapps/ROOT/WEB-INF/classes/$(\
      find /usr/local/tomcat/webapps/ROOT/WEB-INF/classes -name "*.class" | head -1);
    od -An -tx1 -N8 "$f"'                        # -> ca fe ba be 00 00 00 41  (0x41=65=JDK21)
```

### What was actually deployed and verified (2026-06-04, dev03)

A clean 1.10.0 deploy with the **off-the-shelf `xnat-openid-auth-plugin`** (the
stated direction — "Plugin example 2" above), following the patterns in this
doc. Verified facts:

- **The image boots cleanly.** No `UnsupportedClassVersionError`, no "No Spring
  WebApplicationInitializer" — Spring wired up (`InitializingTasksExecutor` ran
  its task chain), Hibernate first-boot DDL completed ("Database initialization
  complete", ~6.5 min), pod went `1/1 Ready` with zero restarts.
  `GET /xapi/siteConfig/buildInfo` reports `version: 1.10.0`.
- **The OpenID plugin loads.** `GET /xapi/plugins` (queried from inside the pod,
  bypassing oauth2-proxy) lists `openIdAuthPlugin`
  (`au.edu.qcif.xnat.auth.openid.OpenIdAuthPlugin`).
- **The OIDC flow initiates correctly.** `GET /openid-login?providerId=keycloak`
  returns **HTTP 302** to
  `https://keycloak.dev03.tag.rcif.io/realms/scout/protocol/openid-connect/auth`
  with `client_id=xnat`, `redirect_uri=https://xnat.dev03.tag.rcif.io/openid-login`,
  `code_challenge_method=S256` (PKCE), `scope=openid profile email`,
  `response_type=code`. That confirms the confidential-client wiring is correct.
- **Browser login works end to end.** After enabling the provider (see below),
  the login page renders the "Sign in with Scout" link; clicking it runs the full
  authorization-code flow against Keycloak and lands on the XNAT dashboard as a
  freshly-provisioned `keycloak-<sub>` user. Driven through `agent-browser`: log
  in to Scout (oauth2-proxy edge gate) as a `scout-user`, navigate to XNAT, click
  the button — no second set of credentials. The silent-SSO hop happens because
  Keycloak already has a session from the Scout login.

> **✅ RESOLVED — the login blocker was a missing `enabledProviders` entry.** The
> first 1.10 deploy showed no SSO link because XNAT's `enabledProviders` site
> setting still held its default `['localdb']`, so `XnatProviderManager` never
> surfaced the discovered `keycloak` provider on the login page. The
> `/openid-login` endpoint worked when hit directly (the 302 above) precisely
> because provider *discovery* is independent of *enablement*. **Fix:** add the
> provider id to `enabledProviders` (`['keycloak']`, dropping `localdb` to leave
> only the SSO button — `admin` still works via the REST API). Seed it in
> `prefs-init.ini` for a fresh deploy, or set it via `/xapi/siteConfig` on a
> running instance. Verified with a full browser round-trip on dev03, 2026-06-04.

**Plugin version — use 1.5.0, not 1.4.1.** The current `xnat-openid-auth-plugin`
release is **1.5.0**, built against `org.nrg:parent` **1.10.0** (the matching
version for XNAT 1.10; 1.4.1 was built against 1.9.2). It was built from source
(`~/repos/openid-auth-plugin`, `./gradlew xnatPluginJar`) **under JDK 21** —
producing JDK 21 bytecode (major 65) that matches the 1.10 runtime. A
JDK-8-compiled plugin would also *load* on the JDK 21 JRE (backward compatible),
but 1.5.0-against-1.10.0 is the right artifact to avoid runtime API drift.

**Delivery used Pattern A (Secret + busybox cp).** The dev03 cluster has
restricted egress — `bitbucket.org` does not even resolve (NXDOMAIN) — so
Pattern B (download from the Bitbucket release URL) is not an option there. The
JAR was built locally and loaded via a Secret. This is also why **Pattern D
(Maven coordinates via a Nexus proxy)** is the gitops-friendly target for an
air-gapped setup: same controlled-proxy story, no hand-created binary Secret.

**What was *not* exercised** (still open before calling 1.10 production-ready):

- The Scout **custom** `scout-xnat-auth-plugin` under 1.10 (this deploy used the
  openid plugin instead; the custom plugin's 1.10.0-BOM/JDK-21 rebuild compiled
  cleanly in the POC but was not run on the 1.10 runtime).

(No 1.9→1.10 data migration is in scope — XNAT has only ever run on dev, never in
production, so 1.10 is a clean deploy with nothing to migrate.)

#### Future enhancement: true auto-redirect (skip the button)

The current login is one click ("Sign in with Scout"). The *ideal* is no click —
hit XNAT and get bounced straight into the Keycloak flow. Two findings bound the
options:

- **Config alone can't do it.** Removing `localdb` from `enabledProviders` only
  hides the username/password form; the openid provider is `isVisible()=false`
  and renders solely as a link, so the page still shows just the button (verified
  live). There is no single-provider auto-redirect in stock XNAT.
- **The login URL is hardcoded.** XNAT's unauthenticated entry point sends users
  to `/app/template/Login.vm` (`SecurityConfig.java`'s
  `loginUrlAuthenticationEntryPoint` bean — a string literal, not a site
  setting). Pointing it at `/openid-login` would auto-redirect, but that's a code
  change.

**Proposed solution — a custom XNAT theme.** XNAT's theme mechanism lets a theme
override the Login screen (`Login.java` honors `themeService.getThemePage("Login")`
→ `themedRedirect`). Ship a tiny theme whose Login page is just a client-side
redirect to `/openid-login?providerId=keycloak` when the user is unauthenticated.
Unauthenticated users then land on the theme page and bounce into the OIDC flow
with no button. This needs the theme delivered with the deploy (a config-volume
artifact / chart-managed Secret, the same gitops concern as `prefs-init.ini`), so
it folds naturally into the future `xnat` Ansible role rather than the manual POC.
Not implemented; the one-click button is the accepted interim UX.

### Sketch: upgrading the Scout XNAT setup to 1.10

Small, given the above. To carry this into the eventual `xnat` Ansible role (see
"Toward Ansible automation"):

1. **Image tag → `1.10.0`** in the values (`image.tag`). No chart change needed;
   the chart takes the tag override (its `appVersion` stays 1.9.2.1).
2. **Plugin:** ship `xnat-openid-auth-plugin` **1.5.0** (built against 1.10.0
   under JDK 21). If instead keeping the custom Scout plugin, rebuild it against
   the **1.10.0 BOM under a JDK 21 toolchain** (already validated to compile).
3. **Keycloak `xnat` client must be confidential** for the openid plugin — a
   secret plus the `/openid-login` redirect URI (see "The full-featured `xnat`
   Keycloak client"). The POC's bearer-only client is the wrong shape for this
   path.
4. **Provider config** via `authplugins: { keycloak: { provider: openid, auth:
   true } }` + a `xnat-plugin-keycloak` Secret (key `openid-provider-properties`)
   — verified to mount at `config/auth/openid-provider.properties`.
5. **Enable the provider:** `enabledProviders=['keycloak']` in `prefs-init.ini`'s
   `[siteConfig]` (or via `/xapi/siteConfig` on an already-initialized instance).
   **This is mandatory** — without it the login page shows no SSO link. This was
   the blocker on the first 1.10 deploy.
6. **Verify:** clean boot (the signatures above), plugin in `/xapi/plugins`,
   `/openid-login` 302, the "Sign in with Scout" link on the login page, and a
   full browser login landing on the dashboard as `keycloak-<sub>`.

## Known gaps / deferred (from the POC)

- Postfix subchart installs but `smtpEnabled=false`; outbound mail unwired
  (MailHog routing is a follow-up).
- `redis.enabled: false`, ActiveMQ disabled — fine at `replicaCount: 1`,
  revisit before HA.
- DICOM C-STORE listener not exposed (`service.type: ClusterIP`); DICOMweb under
  `/xapi/dicomweb/...` is the assumed ingest path.
- `local-path` storage pins XNAT to one node — fine for dev.
- Bearer tokens accepted until their own expiry; no revocation check (keep token
  lifetimes short).
- The POC baseline is XNAT 1.9.2.1 / JDK 8, but **1.10.0 / JDK 21 now works** —
  the upstream image is fixed and a 1.10.0 + OpenID-plugin deploy boots, loads
  the plugin, and supports a full browser login on dev03 (see "XNAT 1.10 / JDK 21
  migration"). The earlier login blocker (login page showed no SSO link) was a
  missing `enabledProviders` entry and is **resolved**.
- **Login is one click, not auto-redirect.** The login page shows a "Sign in with
  Scout" button rather than bouncing straight into Keycloak. True auto-redirect
  needs a custom XNAT theme — see "Future enhancement: true auto-redirect" — and
  is deferred.
