# pluggable_apps

Installs the Helm charts a site lists in `pluggable_apps`, after the rest of Scout. Runs
last in `playbooks/main.yaml`, or on its own with `make install-pluggable-apps`. With an
empty list it does nothing.

For the entry fields, what goes in a Pluggable App's chart, and how to install one under
GitOps, see `docs/source/customize/install.md`.

Values, Secrets and registry credentials are not logged, so a failed install reports
little. Re-run with the values in a file and `helm install --dry-run` to see why.

The chart's login leaves helm logged in on the Ansible host, as `helm registry login` does.

## Private images

An entry's `registry` or `registries` logins reach only its own pods, through a
`MutatingAdmissionPolicy` and binding, `pluggable-app-<name>`, scoped to its namespace
(ADR 0041). The `pluggable_app_logins` filter checks the declaration.

- **Connected:** the logins go in a `kubernetes.io/dockerconfigjson` Secret,
  `<name>-registry`, with an `auths` entry per prefix, and the policy adds it to every new
  pod's `imagePullSecrets`.
- **Air-gapped:** `roles/harbor/tasks/app_projects.yaml` gives each login a Harbor registry
  endpoint and a private proxy project, `app-<name>` (then `app-<name>-2`, ...), and the
  policy rewrites matching images to that project. The nodes pull it as Harbor's pull robot,
  which exists once the staging group sets `harbor_pull_robot_password` and `make
  install-staging` and `make install-k3s` have run. The role checks for the robot first.

`state: absent`, or an entry with no logins, deletes the policy and binding. The pull
Secret and the Harbor project and endpoint stay.
