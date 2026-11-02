# pluggable_apps

Installs the Helm charts a site lists in `pluggable_apps`, after the rest of Scout. Runs
last in `playbooks/main.yaml`, or on its own with `make install-pluggable-apps`. With an
empty list it does nothing.

For what goes in a Pluggable App's chart, and how to install one under GitOps, see
`docs/source/customize/install.md`.

## Entry fields

| Field | Required | Notes |
| --- | --- | --- |
| `name` | yes | Helm release name. |
| `namespace` | yes | Created if missing. |
| `chart` | unless `state: absent` | An `oci://` reference or a local chart directory. |
| `version` | for `oci://` charts | The chart version to install. |
| `values` | no | Chart values. May reference vaulted variables and Scout variables such as `server_hostname` and `db_host`. |
| `secrets` | no | Secrets to create in `namespace` before the install, as `{<name>: {<key>: <value>}}`. For charts that read credentials from an existing Secret. |
| `database` | no | `{name, owner, password}`. Creates the role and database in Scout's Postgres, as Scout's own services do. |
| `registry` | no | `{username, password}` for an `oci://` chart in a private registry. Logs helm in on the Ansible host before the install. |
| `timeout` | no | Helm wait timeout, default `5m`. |
| `state` | no | `present` (default) or `absent`, which uninstalls the release. The namespace, Secrets and database are left in place. |

Values, Secrets and registry credentials are not logged, so a failed install reports
little. Re-run with the values in a file and `helm install --dry-run` to see why.

`registry` leaves helm logged in on the Ansible host, as `helm registry login` does.

## Private images

Air-gapped nodes pull every image through Harbor's proxy caches, which pull from the
upstream anonymously. For a private image on `ghcr.io`, set `harbor_ghcr_proxy_username`
and `harbor_ghcr_proxy_password` on the staging group and run `make install-staging`. See
`roles/harbor/defaults/main.yaml`.

Sites that pull images directly need a pull Secret of type `kubernetes.io/dockerconfigjson`,
which `secrets` does not create.
