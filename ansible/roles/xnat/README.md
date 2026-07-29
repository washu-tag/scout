# xnat role

Deploys [XNAT](https://www.xnat.org/) (`xnatworks/xnat-web`) into Scout with one
or more plugins, behind oauth2-proxy, using the off-the-shelf
`xnat-openid-auth-plugin` for Keycloak SSO. Optional and **disabled by default**
(`enable_xnat: false`).

When `enable_xnat` is false the playbook end_plays before this role runs, so
nothing XNAT is created — and the Keycloak realm omits the `xnat` client and the
`xnat-access` role (gated in `scout-realm.json.j2`).

> **Toggle-off caveat:** flipping `enable_xnat` from true back to false deletes
> the `xnat` Keycloak client on the next `make install-auth` (keycloak-config-cli
> reconciles the realm), orphaning provisioned XNAT users.

> **Authorization note:** oauth2-proxy edge approval is the **only** enforced
> authorization gate. The off-the-shelf openid plugin cannot evaluate the
> `xnat-access` client role (it has no role-restriction property), and with
> `forceUserCreate` any approved Scout user gets an XNAT account auto-created
> on first login. `xnat-access` is provisioned but unenforced — see ADR 0026.

## How it deploys

1. **Chart**: the upstream chart is published as an OCI artifact
   (`oci://ghcr.io/nrgxnat/charts/xnat`, `xnat_chart_oci_ref`),
   pinned to `xnat_chart_version` (`group_vars/all/versions.yaml`). Helm pulls it
   directly from GHCR on the jump node — the `deploy_helm_chart` wrapper delegates
   to localhost for `oci://` refs. (Dev override: set `xnat_chart_path` to a local
   chart dir to deploy unpublished edits.)
2. **Secrets** (`create_secrets.yaml`): all Secrets are created by Ansible and
   referenced by name in the templated values — the chart owns no Secrets. This
   includes the first-boot `xnat-prefs-init`, per-plugin config Secrets, and any
   Pattern-A jar Secrets.
3. **Values** (`templates/values.yaml.j2`): templated from inventory, including
   the generated `plugins` / `authplugins` / `extraConfig` / `extraVolumes` blocks
   derived from `xnat_plugins_all`. The role writes no `initContainers` of its
   own — the chart renders one per plugin from the `plugins` map.
4. **Helm install** via the shared `deploy_helm_chart` wrapper, with a 20-minute
   `--wait` to cover Hibernate's first-boot DDL.

## Plugins

`xnat_plugins` (set in inventory) is **additive** over the role's
`xnat_plugins_default` (which carries the required `openid` plugin):

```
xnat_plugins_all = xnat_plugins_default + xnat_plugins
```

So you list only *your* plugins; the openid plugin is never repeated and can't
be accidentally dropped.

Each plugin entry:

```yaml
- name: my-plugin            # init-container name + Secret name suffix
  target: my-plugin.jar      # filename written into the plugins dir
  source:
    type: coordinates        # file | url | coordinates | image
    # type: file        -> secret: {name, key, from_file}   (from_file = jar path on the control node)
    # type: url         -> url: https://...
    # type: coordinates -> coordinates: <Maven -Dartifact coord>, plus optional repo_url
    # type: image       -> repository, tag                       (chart-native plugins: map; no installer)
    # Maven -Dartifact format: groupId:artifactId:version[:packaging[:classifier]]
    # (a `-xpl.jar` is packaging `jar` + classifier `xpl`, hence `...:jar:xpl`).
    coordinates: org.example:my-xnat-plugin:1.2.3:jar:xpl
    # repo_url is OPTIONAL -- omit it for artifacts on Maven Central. Set it only
    # when the artifact lives elsewhere (e.g. the openid plugin in jfrog). It is
    # passed through as the chart's plugins.<name>.mavenUrl; air-gapped deploys
    # mirror ALL resolution through the Nexus group regardless.
    repo_url: https://repo.example.org/releases/
  config:                       # optional; one or more property files
    - mechanism: authplugins    # authplugins | file | extraConfig
      # authplugins: provider, entry (-> Secret xnat-plugin-<entry>), properties{}
      #              chart mounts it at config/auth/<provider>-provider.properties
      # file:        dest (path under /data/xnat/home/), properties{}
      # extraConfig: properties{} merged into xnat-conf.properties
      provider: openid
      entry: keycloak
      properties:
        some.key: value
```

### Plugin delivery

Every source type goes into the chart's native `plugins:` map, and the chart
renders the init container: a stock curl image for `url` / `coordinates` / `file`
(it resolves the coordinate to a URL at render time, so the exact artifact is
visible in `helm template`), and the plugin's own image for `image`. Jars are
installed exactly as published — nothing is unpacked or repackaged.

Because nothing rewrites the plugins' bundled logback configs any more, plugin
logs reach `kubectl logs` only if XNAT itself is told to log to the console: set
`XNAT_LOG_CONSOLE=plain` on the container (chart value `logConsole`). Without it,
plugin logs go to files under `$XNAT_HOME/logs` where nothing collects them.

### Air-gapped notes

- **coordinates** (Pattern D) is the air-gap-correct path: jars resolve through
  the Nexus maven proxy (`maven_proxy_url`), no egress. The role passes that URL
  as the chart's `plugins.<name>.mavenUrl` and names the staging CA Secret via
  `pluginInstaller.caCertSecret` (per ADR-0016) so the fetch trusts Nexus's
  self-signed HTTPS. Release versions only — a `-SNAPSHOT` coordinate is rejected
  at render time, since resolving one needs `maven-metadata.xml`.
- **url** needs egress today, so on air-gapped clusters use coordinates, image or
  file instead. The chart can rewrite url plugins onto a mirror
  (`pluginRepository.baseUrl` replaces a matching prefix, e.g. `https://github.com`),
  but Scout's Nexus defines Maven repositories only — a `raw` proxy of the upstream
  host would have to be added to the nexus role first. See ADR 0027.
- **image** pulls through Harbor like every other Scout image.

## Testing an unreleased WAR / plugins

To test an unreleased XNAT WAR or plugin build against a dev cluster without
publishing a custom image, set local paths in inventory:

```yaml
xnat_dev_war: /path/to/xnat-web-<ver>.war   # optional
xnat_dev_plugins:                            # optional
  - /path/to/<plugin>.jar
```

`make install-xnat` then stages them into MinIO (a throwaway pod copies them in
and uploads them) and the chart's `dev-war` / `dev-plugins` init containers pull
them into the pod at start-up, using its S3 support pointed at MinIO; a
`kubectl rollout restart` picks up a re-staged build with no redeploy. A
side-loaded plugin overrides a declared one of the same filename — `dev-plugins`
runs after the per-plugin installs — so a local build of a plugin already in
`xnat_plugins` wins without editing that list. Requires in-cluster MinIO and a
base image whose JDK matches the WAR (override `xnat_image_tag` via `-e` — it's
pinned above inventory). Full guide:
[`docs/internal/xnat-develop-testing.md`](../../../docs/internal/xnat-develop-testing.md).

## Container service

XNAT's container-service defaults to a local Docker socket (`/var/run/docker.sock`),
which doesn't exist in the pod. Set `xnat_container_service: true` to run it on a
**Kubernetes backend** instead. That does two things:

1. **Chart** `containerService: true` — provisions the CS `Role`/`RoleBinding`s and
   mounts the ServiceAccount token, so CS can launch containers as k8s Jobs.
2. **Post-deploy** (`tasks/configure_cs.yaml`, run by `make install-xnat`) —
   idempotently registers a `DockerServer` with `backend: kubernetes` via XNAT's
   REST API (a `GET` check makes re-runs a no-op). This is what stops the
   docker-socket lookup.

> **Storage / scheduling:** CS Job pods co-mount the `xnat-archive` / `xnat-build`
> PVCs with XNAT. With `ReadWriteOnce` local-path this works without a nodeSelector —
> the PVs' node affinity auto-schedules the Jobs onto XNAT's node, and RWO allows
> multiple pods on one node — but it confines all CS work to that single node (GPU
> CS jobs can't reach a separate GPU node). Only if CS Jobs must spread across nodes
> (or run on a different node than XNAT) do you need **ReadWriteMany** archive/build
> (NFS/beegfs), plus `xnat_cs_swarm_constraints` / `xnat_cs_kubernetes_tolerations`
> to place them.

## Multiple replicas

`replicaCount` is 1. If you scale up, the role has already patched a **Traefik
sticky-session cookie** onto the XNAT Service (deploy.yaml) — without it, the
session-based login bounces between pods and you get kicked back to the launchpad.
A real multi-replica deployment also needs **RWX** `archive`/`build` storage (see
the Container service note); on RWO local-path all replicas pin to one node.

For multi-node **message/event** testing, set `xnat_dev_activemq: true` — the role
deploys a standalone ActiveMQ Artemis broker and points every replica at it via
`spring.activemq.*`, so cluster events share one queue instead of each pod's
embedded broker. Dev/test only (one ephemeral broker, no HA/persistence). See the
runbook (`docs/internal/xnat-develop-testing.md`) for how to inspect delivery.

## Mail

XNAT routes outbound mail through Scout's shared relay (MailHog in dev,
`xnat_smtp_host`/`_port` for an org relay) — the same pattern as Keycloak and
Grafana. The chart's bundled bokysan/postfix subchart is disabled
(`mail.enabled: false` in `values.yaml.j2`), so no per-XNAT mail server runs.

> SMTP is configured in the `[notifications]` section of `prefs-init.ini` with
> flat keys (`smtpHostname`, `smtpPort`, `smtpProtocol`, `smtpEnabled`, …), per
> XNAT's `NotificationsPreferences` — not a `smtpServer` map in `[siteConfig]`.
> `prefs-init.ini` only seeds preferences on **first boot**; afterward change
> them via the admin UI / config service.

## Key variables

See `defaults/main.yaml`. Commonly set in inventory: `enable_xnat`,
`keycloak_xnat_client_secret`, `xnat_postgres_password`, `xnat_admin_password`,
`xnat_site_id`, `xnat_admin_email`, `xnat_smtp_host`, `xnat_plugins`,
`xnat_chart_version`, `xnat_image_tag`.

`keycloak_xnat_client_secret`, `xnat_postgres_password`, and
`xnat_admin_password` are **required** when `enable_xnat` is true; the role
fails the deploy if any is unset. `xnat_admin_password` seeds XNAT's `admin`
account at first boot (`[system] defaultAdminPassword`), so the default
`admin:admin` never survives a fresh deployment.

> **Set `enable_xnat` AND `xnat_plugins` in `all.vars`, not a cluster group.** In
> air-gapped deployments the staging Nexus role derives one `scout-maven` Maven
> proxy per distinct coordinate-plugin `repo_url` (from the shared plugin list,
> including the openid default), gated on `enable_xnat`. The staging host does not
> inherit `k3s_cluster` group vars, so scoping either there leaves the proxies out
> of the group and the affected init container can't resolve its plugin
> (`CrashLoopBackOff`). Because the proxies are built from `xnat_plugins`,
> **re-run `make install-staging` after changing that list** before the XNAT
> deploy; the deploy's air-gapped preflight fails with guidance otherwise. The
> XNAT secrets/site config remain cluster-scoped.
