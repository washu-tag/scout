# ADR 0041: Private Images for Pluggable Apps

**Date:** 2026-11
**Status:** Accepted
**Decision Owner:** TAG Team

## Context

A Pluggable App is a Helm chart that a site installs with the rest of Scout
(`docs/source/customize/install.md`). Some vendors publish an app's chart and
images only to a private registry, for example as private packages on `ghcr.io`
that a token the vendor issues can read. Making them public isn't an option.

Helm fetches an app's chart on the Ansible controller, which can log in with the
entry's credentials. The images are a separate problem, because the nodes pull them:

- **Connected sites.** Nodes pull straight from the registry. Without a credential
  for the image, the pull fails.
- **Air-gapped sites.** Nodes pull every image through the staging node's Harbor
  (ADR 0002). k3s's `registries.yaml` mirrors each upstream registry to one Harbor
  proxy-cache project with a catch-all rewrite. Harbor can hold one credential per
  upstream (`harbor_<registry>_proxy_username` and `_password`), which makes that
  project private.

An upstream credential in Harbor would make an app's token the credential for every
pull from that registry, Scout's own included. When the token expired or was
revoked, Scout's own images would stop pulling too. A second app, with its own
account on the same registry, couldn't be supported at all.

Requirements:

1. An app's credential is used only for that app's images. When it expires, only
   that app's pulls fail.
2. Different apps, or different accounts within one app, can use different
   credentials on the same registry host.
3. The app's chart and its values aren't changed, and nothing depends on how the
   chart runs its pods, such as which ServiceAccount they use.
4. Adding or removing an app, or rotating its token, doesn't restart nodes.
5. An operator declares a private source the same way on connected and air-gapped
   sites.

## Decision

**One declaration: logins, each with the image prefixes it covers.** A
`pluggable_apps` entry takes a list of logins:

```yaml
registries:
  - prefixes: [ghcr.io/example-vendor]
    username: '{{ example_vendor_username }}'
    password: '{{ example_vendor_token }}'
  - prefixes: [ghcr.io/other-vendor]
    username: '{{ other_vendor_username }}'
    password: '{{ other_vendor_token }}'
```

- The chart, and each image, uses the login with a prefix it starts with. Prefixes
  match whole path segments.
- A login's prefixes are all on one host, since a credential belongs to one
  registry. Prefixes within an entry don't nest.
- `registry: {username, password}` is shorthand for a single login whose prefix is
  the chart's registry host. Every image on that host, pulled by the app's pods,
  uses it.
- A local chart directory can declare logins too, for its images, with explicit
  prefixes.

Scout can't work these out for itself. The app's developer knows which images it
pulls, and the deployer knows which login can read them. Rendering the chart lists
the images in its pod templates, but not which login each needs, and it misses
images that a component picks at runtime. A preflight that renders the chart and
warns about images no login covers can be added later.

**An admission policy per app.** For each app with a login, the pluggable_apps role
creates a `MutatingAdmissionPolicy` and a binding scoped to the app's namespace,
before it installs the chart. The policy changes each Pod as it's created, whatever
ServiceAccount the Pod runs as. The Deployment, and the release that Helm or Flux
manages, keep the chart's spec, so neither sees drift. The API server evaluates the
policy itself, so no webhook has to be running. `MutatingAdmissionPolicy` is GA from
Kubernetes 1.36, and Scout's k3s serves it by default. What the policy changes
depends on the site.

**Connected sites: the policy adds a pull Secret.**

- The role puts the logins in a `kubernetes.io/dockerconfigjson` Secret,
  `<name>-registry`, in the app's namespace, with one `auths` entry per prefix.
  Kubelet matches those entries by path prefix, so each image gets its own login.
  Kubelet compares raw strings, so an entry for a path ends in `/`.
- The policy adds the Secret to `imagePullSecrets` on every Pod in the namespace,
  keeping any the Pod already names or gets from its ServiceAccount.
- Only pods in that namespace send the token. Scout's own pods never do.

**Air-gapped sites: the policy sends the app's images to its own Harbor project.**

- During `make install-pluggable-apps`, the role creates, in the staging node's
  Harbor, a registry endpoint for each login, holding its credential, and a private
  proxy-cache project linked to it. Harbor allows several endpoints with the same
  URL; only their names must be unique. It checks a credential against the upstream
  whenever an endpoint is created or updated, and rejects one that fails.
- The policy rewrites each `containers` and `initContainers` image that starts with
  a prefix to `<harbor_host>/<project>/<path>`, where `<path>` is the image without
  its host.
- Nodes pull the rewritten image straight from Harbor. The Harbor host has no mirror
  entry, so no rewrite rule applies to it, and k3s's
  `disable-default-registry-endpoint` affects only registries that have one. Nodes
  log in as the pull robot (below).
- The role creates no pull Secret on air-gapped sites, so the token never enters the
  cluster. A Secret for the upstream host would do harm there: from containerd's
  source, it sends the credentials kubelet passes for an image to every endpoint it
  tries, so Harbor would get the vendor's token in place of the robot's.

**The nodes' Harbor login.** Whenever the staging group sets
`harbor_pull_robot_password`, Harbor has a pull-only robot that can read every
project, and `make install-k3s` gives the nodes its login. A site sets it once.
Every private project needs it: an air-gapped app's, and an upstream proxy's with a
credential. The pluggable_apps role won't install an air-gapped app with a login
until the robot exists. A later app needs no robot or node change.

**`registries.yaml` keeps its job.** It stays the default, anonymous route for every
image, and it carries the Harbor host's trust and the robot's login. It changes only
when Scout adds an upstream registry or the robot's password changes. The admission
policy doesn't replace it. Some pulls never pass admission, such as k3s's pause image
or `crictl pull`. And a cluster-wide policy that rewrote every pod would put pod
creation in every namespace, `kube-system` included, at the mercy of one policy.

**Upstream credentials stay, for Scout's own use.** `harbor_<registry>_proxy_*`
remains for a credential that applies to a whole upstream, such as a Docker Hub
account that raises the pull rate limit. The Pluggable App docs don't point at it,
and `ghcr-proxy` stays public and anonymous.

**Removing an app.** `state: absent` deletes the app's policy and binding along with
its release. Like its namespace, Secrets and database, the pull Secret and the Harbor
project and endpoint stay.

**Rotation.** Update the inventory and run `make install-pluggable-apps`. On a
connected site, that updates the Secret, and later pulls use the new token. On an
air-gapped site, it updates the Harbor endpoint, which Harbor reads on every proxied
request. Neither restarts a node or changes the Helm release.

**When a token expires.** Running pods are unaffected, as are pods whose image is
already on their node. An air-gapped site keeps serving the tags Harbor has cached;
by default, a proxy project keeps images that were pulled in the last 7 days. New
pulls of anything else fail, but only in that app's namespace.

### GitOps (ADR 0031)

The policy and its binding are cluster objects that go in the site repo beside the
app. A connected site adds the pull Secret to the app's namespace too, encrypted like
its other Secrets, and the app's `OCIRepository` can name the same Secret in
`secretRef`. On an air-gapped site, the `OCIRepository` URL names the app's Harbor
project, with the robot's login in `secretRef`. The Harbor endpoint and project are
staging-node configuration, which ADR 0031 keeps in Ansible.

## Consequences

- An operator declares an app's private sources once, the same way on connected and
  air-gapped sites. The common case, one login for one vendor, stays the one-line
  `registry`.
- One app's expired or revoked token affects only that app. Scout's images, and
  other apps, are untouched.
- On connected sites, the token is in a Secret in the app's namespace. Like every
  Secret, it's stored unencrypted in the datastore, since Scout doesn't enable k3s
  secrets encryption.
- Pods created before the policy existed aren't changed. After adding a login to an
  installed app, delete its pods so they're recreated.
- A pod differs from the Deployment that made it: it lists one more pull Secret, or
  its images name the Harbor project. Debug containers and image volumes aren't
  changed, and an image written without a registry host (Docker Hub's short form)
  matches no prefix.
- The policy applies to every pod in the app's namespace, so another app installed
  there gets the same pull Secret or the same rewrites. If the policy fails, pods in
  that namespace can't be created; other namespaces are unaffected.
- Any pod that names an app's Harbor project can pull its images, because the node
  robot can read every project. A private upstream proxy has the same exposure.
- An air-gapped `make install-pluggable-apps` calls Harbor's API from the staging
  node, as the Grafana role does for its plugins.
- A site that set `harbor_ghcr_proxy_*` for an app has to undo it by hand, because
  the harbor role only ever makes a project private. Clear the endpoint's
  credential, delete the app's repositories from `ghcr-proxy`, and only then make
  the project public again. In the other order, the app's private images become
  pullable anonymously from Harbor.

## Alternatives Considered

- **An upstream credential in Harbor.** It fails requirements 1 and 2.
- **More specific rewrites in `registries.yaml`,** sending `ghcr.io/example-vendor/`
  to the app's project ahead of the catch-all. k3s's containerd stores a mirror's
  rewrites in a Go map and applies the first that matches, so overlapping rules are
  applied in random order. In a test with containerd's own resolver, 200 pulls sent
  30 to the app's project and 170 to `ghcr-proxy`, and in 43 of them the manifest
  and the layers came from different projects. A generated catch-all that matches
  none of the apps' prefixes avoids the overlap, but it's unreadable (about 190
  characters for one prefix), and prefixes must be disjoint across the cluster. A
  backslash in it makes the generated `hosts.toml` invalid, after which containerd
  silently pulls from the upstream. Every change also restarts k3s on every node,
  which fails requirement 4.
- **Several mirror endpoints, or endpoint path overrides.** They choose by host, not
  by path, and containerd moves to the next endpoint on any 4xx. Every `ghcr.io`
  pull would try an app's credential first, which fails requirements 1 and 2.
- **A node-level credential on connected sites,** in `registries.yaml` or a kubelet
  credential provider. Every pod on the node uses it, and the token sits on every
  node. A `registries.yaml` login also covers a whole host and needs a restart to
  change.
- **The pull Secret on the namespace's `default` ServiceAccount,** on connected
  sites. Kubernetes copies a ServiceAccount's pull Secrets into each new pod that
  runs as it and names none of its own, so no policy would be needed. But a chart
  whose pods run as their own ServiceAccount, or name their own pull Secrets, would
  never get it, which fails requirement 3.
- **Image references overridden in the chart's values.** Each chart names its images
  differently, and the entry would carry Scout-specific edits to a vendor's values.
  It fails requirement 3.
- **A Harbor robot per app,** which the policy would add to the app's pods as a pull
  Secret, so the node robot couldn't read app projects. Each app would bring another
  credential to create, store in the cluster and rotate, while any pod on a node can
  already pull whatever the node can.
- **One cluster-wide policy that routes every image, in place of
  `registries.yaml`.** See the Decision.
- **An admission webhook, such as Kyverno.** It has the same effect as the in-tree
  policy, plus a component that must be running before any pod can be created.
- **A `hosts.toml` written directly, outside `registries.yaml`.** containerd re-reads
  it on every pull, so changes need no restart. But it still needs the generated
  catch-all, and it ties Scout to k3s's internal layout.

## Related

- ADR 0002 K3s air-gapped deployment (Harbor pull-through proxy caches)
- ADR 0031 GitOps
- `docs/source/customize/install.md`
