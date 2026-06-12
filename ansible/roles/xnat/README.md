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

1. **Chart**: the upstream chart is not yet published, so `fetch_chart.yaml`
   clones `NrgXnat/helm-charts` at a pinned tag (`xnat_chart_git_ref`) on the
   jump node and runs `helm dependency update`. (When upstream publishes a chart
   repo/OCI artifact, replace `fetch_chart.yaml` with a `helm_repo_url` + version
   ref.)
2. **Secrets** (`create_secrets.yaml`): all Secrets are created by Ansible and
   referenced by name in the templated values — the chart owns no Secrets. This
   includes the first-boot `xnat-prefs-init`, per-plugin config Secrets, any
   Pattern-A jar Secrets, and a placeholder `postfix-password` (see Mail below).
3. **Values** (`templates/values.yaml.j2`): templated from inventory, including
   the generated `initContainers` / `plugins` / `authplugins` / `extraConfig` /
   `extraVolumes` blocks derived from `xnat_plugins_all`.
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
    # when the artifact lives elsewhere (e.g. the openid plugin in jfrog). When
    # set, the deploy mounts a settings.xml pointing Maven at it; air-gapped
    # deploys mirror ALL resolution through the Nexus group regardless.
    repo_url: https://repo.example.org/releases/
  skip_logback_rewrite: false   # default false
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

### Plugin delivery & logback rewrite

For `file` / `url` / `coordinates` sources, the role generates an init container
running the Scout **xnat-plugin-installer** image
(`docker/xnat-plugin-installer/`). It acquires the jar, **rewrites the plugin's
bundled logback config to log to stdout** (XNAT plugins ship a
RollingFileAppender; Kubernetes wants stdout), and copies it into the shared
`home-plugins` volume. `image` sources use the chart's native `plugins:` map and
are assumed pre-built to log to stdout.

### Air-gapped notes

- **coordinates** (Pattern D) is the air-gap-correct path: jars resolve through
  the Nexus maven proxy (`maven_proxy_url`), no egress. The role mounts a
  generated `settings.xml` that mirrors all Maven resolution through the Nexus
  group, plus the staging CA (per ADR-0016) so the installer trusts Nexus's
  self-signed HTTPS. The installer image carries no repo/air-gapped knowledge —
  it just uses the `settings.xml` and CA when mounted (see ADR 0027).
- **url** needs egress, so on air-gapped clusters it fails fast — use
  coordinates, image, or file instead (re-hosting url jars via Nexus is a
  possible future enhancement; see ADR 0027).
- **image** pulls through Harbor like every other Scout image.

## Mail

XNAT routes outbound mail through Scout's shared relay (MailHog in dev,
`xnat_smtp_host`/`_port` for an org relay) — the same pattern as Keycloak and
Grafana. The upstream chart still pulls the bokysan/postfix subchart
unconditionally, so a placeholder `postfix-password` Secret is created to let
that pod start; XNAT does not route through it. Once the upstream
`condition: mail.enabled` change lands, set `mail.enabled: false` and drop the
placeholder.

> SMTP is configured in the `[notifications]` section of `prefs-init.ini` with
> flat keys (`smtpHostname`, `smtpPort`, `smtpProtocol`, `smtpEnabled`, …), per
> XNAT's `NotificationsPreferences` — not a `smtpServer` map in `[siteConfig]`.
> `prefs-init.ini` only seeds preferences on **first boot**; afterward change
> them via the admin UI / config service.

## Key variables

See `defaults/main.yaml`. Commonly set in inventory: `enable_xnat`,
`keycloak_xnat_client_secret`, `xnat_postgres_password`, `xnat_site_id`,
`xnat_admin_email`, `xnat_smtp_host`, `xnat_plugins`, `xnat_chart_git_ref`,
`xnat_image_tag`.

> **Set `enable_xnat` in `all.vars`, not a cluster group.** In air-gapped
> deployments the staging Nexus role gates the `xnat-maven` plugin proxy's
> membership in the `scout-maven` group on `enable_xnat`. The staging host does
> not inherit `k3s_cluster` group vars, so scoping `enable_xnat` there leaves the
> proxy out of the group and XNAT's openid init container can't resolve its
> plugin (`CrashLoopBackOff`). The XNAT secrets/site config remain cluster-scoped.

See `docs/internal/xnat-and-plugin-deployment.md` for the full deployment
reference and upstream-contribution notes.
