# Testing an Unreleased XNAT WAR + Plugins

This guide explains how to run an **unreleased XNAT web application (WAR) and/or
unreleased plugin JARs** on a Scout cluster without publishing a custom image,
using the `xnat` role's built-in side-load support. Use it to verify an XNAT
bugfix or plugin change against a real Scout deployment (Keycloak, Traefik, CNPG,
MinIO all wired up) before the build is released.

> **Scope.** Development / test only. Point it at a synthetic-data dev cluster
> (`tagdev-*`), never pre-prod or prod.

## How it works

XNAT's WAR is baked into the runtime image and exploded under
`/usr/local/tomcat/webapps/ROOT` at boot; plugins live in
`/data/xnat/home/plugins`. The role overrides both **without rebuilding the
image**:

1. You point `xnat_dev_war` / `xnat_dev_plugins` at local files (on the Ansible
   control node).
2. On `make install-xnat`, `tasks/dev_stage.yaml` stages them into MinIO: a
   throwaway pod is created with a **busybox** sidecar (which has `tar`, required
   by `kubectl cp`; the `mc` image doesn't) and an **mc** container; the files
   are `kubectl cp`'d into busybox and `mc`-uploaded to the `xnat_dev_bucket`
   (`xnat-dev` by default), then the pod is torn down.
3. The chart mounts two generated init containers (`templates/values.yaml.j2`):
   - **`develop-war`** — an `emptyDir` shadows `/usr/local/tomcat/webapps`, and
     this container pulls `ROOT.war` into it. Tomcat auto-explodes and serves it.
   - **`develop-plugins`** — `mc mirror`s the staged jars into the shared
     `home-plugins` volume.

Because the init containers re-pull on every start, re-staging a new build +
`kubectl rollout restart` is enough to re-test — no full redeploy.

## Choosing the base image

The WAR is swapped, but the base image still supplies the JVM + servlet
container, so **its JDK must match the WAR**:

- XNAT **1.9.x** WARs are **JDK 8** → use a `1.9.x` base
  (e.g. `ghcr.io/nrgxnat/xnat-web:1.9.3.6`).
- XNAT **1.10.x** WARs are **JDK 21** → use a `1.10.x` base.

Prefer the released patch nearest your build (base `1.9.3.6` under a `1.9.3.7-RC`
WAR).

> `xnat_image_tag` is pinned in `group_vars/all/versions.yaml`, which **outranks
> inventory** (see the variable-precedence note in the root `CLAUDE.md`), and it
> defaults to a **1.10** tag (JDK 21). Setting it in inventory has no effect —
> override it (and the repository) with `-e` on the deploy command. Boot a 1.9.x
> WAR on the default 1.10 base and it fails on the wrong JDK.

## Procedure

Example commands target the air-gapped **dev04** cluster; adjust the context and
image for your environment.

### 1. Set the artifacts in inventory

Paths are read on the host that runs the staging (delegated like the Helm
deploy): the **Ansible control node** in air-gapped mode (where you built the
WAR), or the target cluster node otherwise.

```yaml
xnat_dev_war: ~/XNAT/xnat-web/build/libs/xnat-web-1.9.3.7-RC-SNAPSHOT.war
xnat_dev_plugins:
  - ~/XNAT/<plugin>/build/libs/<plugin>-<ver>.jar
```

**Replace vs. add plugins.** By default the role's `xnat_plugins_default` carries
the Keycloak SSO (openid) plugin. To test *only* your jars (e.g. a plugin bugfix)
and sign in as the built-in `admin`, "replace" the set:

```yaml
xnat_plugins_default: []       # drop the coordinate-resolved openid jar
xnat_plugins: []
xnat_enabled_providers: [localdb]   # XNAT's own login form (edge oauth2-proxy
                                    # still enforces Keycloak SSO independently)
```

To keep SSO and *add* your jars, leave those alone — `xnat_dev_plugins` mirrors
alongside the role's plugins.

**Side-loading an unreleased auth (SSO) plugin.** A side-loaded jar is delivered
without config, but an auth plugin also needs its provider properties (at
`config/auth/<provider>-provider.properties`). Side-load the jar via
`xnat_dev_plugins` and supply the config through a **config-only** plugin entry —
`source.type: none` installs no jar, only its `authplugins` config flows through.
This lets you drop the role's coordinate-resolved default and wire SSO to your
build instead:

```yaml
xnat_dev_plugins:
  - ~/XNAT/openid-auth-plugin/build/libs/openid-auth-plugin-1.6.0-SNAPSHOT-xpl.jar
  - ~/XNAT/container-service/build/libs/container-service-3.8.1-fat.jar
xnat_plugins_default: []          # drop the coordinate-resolved openid 1.5.0
xnat_plugins:
  - name: openid-sso-config
    source: { type: none }        # config only — no jar installed
    config:
      - mechanism: authplugins
        provider: openid
        entry: keycloak
        properties: { ... }        # copy the role default's openid.keycloak.* block
xnat_enabled_providers: [keycloak, localdb]
```

`enable_xnat: true` and the required XNAT secrets
(`keycloak_xnat_client_secret`, `xnat_postgres_password`, `xnat_admin_password`)
must already be set — see [`../../ansible/roles/xnat/README.md`](../../ansible/roles/xnat/README.md).

### 2. Deploy (overriding the base image on the command line)

```bash
cd ansible
make install-xnat ADD="-e xnat_image_repository=ghcr.io/nrgxnat/xnat-web \
                       -e xnat_image_tag=1.9.3.6"
# dev04 also needs FQDN=all, --ask-become-pass, and -e ansible_user=<user>:
#   make install-xnat FQDN=all ADD="--ask-become-pass -e ansible_user=<user> \
#     -e xnat_image_repository=ghcr.io/nrgxnat/xnat-web -e xnat_image_tag=1.9.3.6"
```

The play stages the artifacts (watch for the `xnat-dev-stage` pod), then Helm
installs XNAT with the side-load init containers.

### 3. Verify

```bash
kubectl --context <ctx> -n xnat get pods

# init containers should Complete: develop-war, develop-plugins, then home-init
kubectl --context <ctx> -n xnat logs xnat-0 -c develop-war
kubectl --context <ctx> -n xnat logs xnat-0 -c develop-plugins

# main container: watch the WAR deploy + Tomcat come up
kubectl --context <ctx> -n xnat logs xnat-0 -c xnat -f
```

Browse to `https://xnat.<external_url>/`, sign in through Scout at the edge, then
log into XNAT (as `admin` with `xnat_admin_password` in "replace" mode). Confirm
the running version under **Administer → Site Administration**, or
`GET /xapi/siteConfig/buildInfo`. Exercise the bugfix.

## Iterating on a new build

```bash
# Re-stage the new artifacts, then restart — no redeploy:
make install-xnat ADD="... -e xnat_image_tag=1.9.3.6"   # re-runs staging
kubectl --context <ctx> -n xnat rollout restart statefulset/xnat
```

(Or upload directly with `mc`/`kubectl cp` if you prefer a tighter loop; the init
containers just read `s3://xnat-dev/ROOT.war` and `s3://xnat-dev/plugins/`.)

## Multi-node testing (external ActiveMQ)

XNAT's per-JVM embedded broker means that if you bump `replicaCount`, each pod
holds its own message queue and cluster events (cache invalidations, the site
anon-script reload, etc.) don't coordinate across nodes. Set
`xnat_dev_activemq: true` in inventory: the role deploys a standalone ActiveMQ
Artemis broker (`xnat-activemq`) and points every replica at it via
`spring.activemq.*`, so the whole cluster shares one queue.

> The role also patches a Traefik sticky-session cookie automatically (so
> multi-replica login doesn't bounce between pods). A genuine multi-node run
> additionally needs RWX `archive`/`build` storage — see the role README.

Confirm a broadcast is actually delivered (once per node, no redelivery) from the
broker's own counters. XNAT's cluster events ride the `dist-events` MULTICAST
address, one subscription per node:

```bash
POD=$(kubectl --context <ctx> -n xnat get pod \
  -l app.kubernetes.io/name=xnat-activemq -o jsonpath='{.items[0].metadata.name}')
kubectl --context <ctx> -n xnat exec "$POD" -- \
  /var/lib/artemis-instance/bin/artemis queue stat \
  --user xnat --password xnatactivemq --maxRows 500 --maxColumnSize 1000
```

In the `dist-events` rows, `MESSAGES ADDED == MESSAGES ACKED` with
`MESSAGE COUNT`, `DELIVERING`, and `DLQ` all 0 means every broadcast was delivered
and consumed exactly once — no broker-level redelivery. To attribute a specific
event: snapshot the counts, trigger it once (e.g. save the site anonymization
script), and re-read — the subscriptions for that event should each climb by
exactly 1 per node (a jump of 2+ on one subscription = that node got it twice).

## Teardown / revert

- **Back to the released image + SSO:** delete the `xnat_dev_*` (and any
  `xnat_plugins_default: []` / `xnat_enabled_providers`) lines and re-run
  `make install-xnat` without the image `-e` overrides.
- **Remove XNAT entirely:** `enable_xnat: false` (note the toggle-off caveat in
  the role README) and, if desired, `kubectl delete ns xnat`.
- Optional: `kubectl -n xnat delete secret xnat-dev-minio`, and drop the
  `xnat-dev` bucket in MinIO.

## Notes

- **Air-gapped.** Air-gap-safe: artifacts come from in-cluster MinIO, and all
  images (`busybox`, `quay.io/minio/mc`, `ghcr.io/nrgxnat/xnat-web`) pull through
  the Harbor mirror. No registry push, no `url`-source plugins.
- **First-run caveat.** The staging step uses `kubernetes.core.k8s_cp` (a
  `kubectl cp` under the hood) for the ~200 MB WAR. If it struggles with the file
  size on your cluster, upload the WAR once by hand
  (`kubectl -n scout-data port-forward svc/minio 9000:80` + `mc cp … s3://xnat-dev/ROOT.war`)
  and the init containers will still pick it up.
- **If the WAR doesn't deploy, or you want app logs on stdout.** `develop-war`
  drops `ROOT.war` and relies on Tomcat's `autoDeploy`/`unpackWARs` (on by
  default) to explode it, and the WAR logs to a rolling file inside the
  container. If the site never comes up (autoDeploy disabled) or you want XNAT's
  logs in `kubectl logs`, explode + patch logback on your workstation and stage
  the exploded tree: `unzip ROOT.war -d ROOT/`, optionally
  `sed -i 's/RollingFileAppender/ConsoleAppender/' ROOT/WEB-INF/classes/logback.xml`,
  upload `ROOT/` to `s3://xnat-dev/ROOT/`, and change `develop-war` to
  `mc mirror` it to `/webapps/ROOT/`. Serves the exploded dir directly (no
  autoDeploy) and keeps the edit workstation-side — no in-cluster `unzip`.
- **Plugin logs on stdout.** A raw plugin JAR logs to a rolling file; the
  `develop-plugins` `mc mirror` does not run Scout's normal logback-to-stdout
  rewrite. Rewrite it before upload, or read the file via `kubectl exec`.
- **GitOps lane.** On the Flux/EKS clusters the same seam is reached through the
  HelmRelease `values` (`extraVolumes` / `initContainers`), with S3 via the pod's
  IRSA role instead of the `mc`/MinIO steps here. The WAR-shadow technique is
  identical.
