# Install a Pluggable App

A Pluggable App is a Helm chart that a site installs into its Scout cluster. The other pages
in this section cover what goes in the chart. This one covers installing it from the site's
own configuration, so it deploys with the rest of Scout rather than as a separate step.

Scout discovers launchpad chips, report-viewer buttons, Keycloak fragments and Grafana
dashboards while it runs, so an app can be installed, upgraded or removed at any time.

## With Ansible

List the app in `inventory.yaml`:

```yaml
pluggable_apps:
  - name: example-app
    namespace: scout-example
    chart: oci://ghcr.io/example-org/charts/example-app
    version: 1.2.0
    values:
      domain: '{{ server_hostname }}'
      database:
        existingSecret: example-app-database
    secrets:
      example-app-database:
        url: 'postgresql://example_app:{{ example_app_db_password }}@{{ db_host }}:{{ db_port }}/example_app'
    database:
      name: example_app
      owner: example_app
      password: '{{ example_app_db_password }}'
```

`make all` installs it last, after every Scout service; `make install-pluggable-apps`
installs only the apps. To remove one, set `state: absent` on its entry and run
`make install-pluggable-apps` again. That uninstalls the release and leaves its namespace,
Secrets and database in place.

The full list of fields is in the [role README](https://github.com/washu-tag/scout/tree/main/ansible/roles/pluggable_apps/README.md).

### Private registries

- **The chart.** Add `registry: {username, password}` to the entry, and helm logs in before
  installing it.
- **Images, on an air-gapped site.** Nodes pull images through the staging node's Harbor
  proxy caches, which pull from the upstream anonymously. For a private image on
  `ghcr.io`, set `harbor_ghcr_proxy_username` and `harbor_ghcr_proxy_password` on the
  staging group, vault-encrypted, and run `make install-staging`. Harbor checks them against
  the upstream, and rejects ones it can't use with a 400. The chart keeps its
  `ghcr.io` image references, and no pull Secret is needed. Harbor's proxy projects are
  public, so anyone who can reach Harbor can pull what those credentials can read: use a
  read-only token from an account that can read only the packages the site needs.

## With GitOps

A Flux-managed site adds its apps to its own site repo, alongside the Kustomizations that
reconcile Scout:

- A Kustomization in `flux-system` for the site's apps, with
  `postBuild.substituteFrom` naming `cluster-vars` so app manifests can use the same
  `${var}` values as Scout's.
- For each app, its Namespace, its Secrets (SOPS-encrypted on-prem, External Secrets in
  aws), an `OCIRepository` for the chart pinned by digest, and a `HelmRelease` that
  references it with `chartRef`. The `HelmRelease` values are the same as the Ansible
  entry's `values`.
- `dependsOn` only for Scout services the app needs when it starts, such as
  `postgres-ready` for an app that connects to Postgres.

[`examples/on-prem-pluggable-app/flux`](https://github.com/washu-tag/scout/tree/main/examples/on-prem-pluggable-app/flux)
lays out one app this way. To remove an app, delete it from the site repo: Flux uninstalls
the release and, with `prune`, deletes its Namespace and everything in it, Secrets
included. That differs from `state: absent` under Ansible.

On an air-gapped site, Flux fetches charts itself rather than through the nodes' registry
mirror, so the `OCIRepository` URL names the Harbor proxy project. Images keep their
`ghcr.io` references, and private ones need the same Harbor credentials as above.

### Databases

An app's database goes in Scout's Postgres cluster. The site adds three resources for it to
the cluster's namespace, beside the app's own:

- A `kubernetes.io/basic-auth` Secret with the app's `username` and `password`, labelled
  `cnpg.io/reload: 'true'` so a password change applies at once. The app reads the same
  password from a Secret in its own namespace, so seed both from one source.
- A `DatabaseRole` for the app's login, and a `Database` that it owns:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: DatabaseRole
metadata:
  name: example-app
  namespace: '${postgres_cluster_namespace}'
spec:
  cluster:
    name: '${postgres_cluster_name}'
  name: example_app
  login: true
  passwordSecret:
    name: cnpg-role-example-app
  databaseRoleReclaimPolicy: retain
---
apiVersion: postgresql.cnpg.io/v1
kind: Database
metadata:
  name: example-app
  namespace: '${postgres_cluster_namespace}'
spec:
  cluster:
    name: '${postgres_cluster_name}'
  name: example_app
  owner: example_app
  databaseReclaimPolicy: retain
```

Add `postgres-ready` to the Kustomization's `dependsOn`, so the cluster exists first. With
`retain`, removing the app from the site repo leaves its role and database in place.
