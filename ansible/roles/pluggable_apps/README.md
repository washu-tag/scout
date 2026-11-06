# pluggable_apps

Installs the Helm charts a site lists in `pluggable_apps`, after the rest of Scout. Runs
last in `playbooks/main.yaml`, or on its own with `make install-pluggable-apps`. With an
empty list it does nothing.

For the entry fields, what goes in a Pluggable App's chart, and how to install one under
GitOps, see `docs/source/customize/install.md`.

Values, Secrets and registry credentials are not logged, so a failed install reports
little. Re-run with the values in a file and `helm install --dry-run` to see why.

`registry` leaves helm logged in on the Ansible host, as `helm registry login` does.

## Private images

Air-gapped nodes pull every image through Harbor's proxy caches, which pull from the
upstream anonymously. For a private image on `ghcr.io`, set `harbor_ghcr_proxy_username`,
`harbor_ghcr_proxy_password` and `harbor_pull_robot_password` on the staging group, then run
`make install-staging` and `make install-k3s`. See `roles/harbor/defaults/main.yaml`.

Sites that pull images directly need a pull Secret of type `kubernetes.io/dockerconfigjson`,
which `secrets` does not create.
