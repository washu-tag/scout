# ADR 0034: Runtime-Configurable Launchpad Catalog

**Date:** 2026-08
**Status:** Accepted
**Decision Owner:** TAG Team

## Context

This is the first step towards architecting Scout for Pluggable Apps: components that
live outside the Scout monorepo, can be customized and installed per site,
and operate as first-class members of the Scout platform. There are several limitations
in our current design that limit or prevent this. Over time we will be removing these
barriers and building new systems to enable Pluggable Apps.

This step is about Launchpad. It is the "front door" of the Scout platform. When a user
arrives at Scout, they land here before going off to their destination. If a newly 
installed component wants any users to find it, it needs to show up here. But that is not
currently possible. Why?

Currently every tile that shows up on the Launchpad is hard-coded. The code, which lives
in the Scout monorepo, must be modified, committed, compiled, built into an image,
published, and deployed for any changes to show up. If I'm a third-party Scout site admin
who wants to add a component to the Launchpad, I have no hope other than forking the repo
and making invasive changes.

Some of the components are customizable, but only minimally in terms of their presence
or absence. There are environmental variables to show things like chat (`ENABLE_CHAT`),
and some of the groups will only appear for admins. But that does not allow anything new
to show up, or anything to be removed that doesn't have an environment variable switch.

We have an example of a service already in Scout that does this kind of runtime discovery: 
Grafana imports any ConfigMap labelled `grafana_dashboard: "1"` through its
`kiwigrid/k8s-sidecar` containers. This means any `ConfigMap` so labeled becomes a dashboard 
within seconds of creation.

## Decision

Chips (tiles) and the groups (page sections) that hold them become **data discovered at
runtime from labelled ConfigMaps**. The launchpad becomes a renderer of that data.
Presence on the page becomes a side effect of installation: the component that *is* the
service ships its chip, and uninstalling the component removes the chip (contrast the
orphaning teardown ADR 0026 documents for imperative registration).

### The contract: a labelled ConfigMap

Any ConfigMap, in any namespace, labelled
**`launchpad.scout.xnat.org/catalog: "true"`**. Each data key holds one YAML catalog
document:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-service-launchpad # convention: <component>-launchpad
  namespace: my-service
  labels:
    launchpad.scout.xnat.org/catalog: 'true'
data:
  apps.yaml: | # any number of keys; each is an independent document
    apiVersion: launchpad.scout.xnat.org/v1alpha1
    kind: Catalog
    chips:
      - id: my-service
        title: My Service
        description: One line about what it does
        icon: beaker
        tone: violet
        link: { subdomain: my-service }
        group: more
    groups: []   # define a group only when introducing a new section
```

Documents identify themselves the way a Kubernetes object does, by the pair
(`apiVersion`, `kind`): a group/version names the API surface, `kind` names the type
within it. The group is scoped to the launchpad rather than to Scout as a whole, so this
schema versions on its own schedule — anything else Scout publishes gets its own group
(`<area>.scout.xnat.org`) and bumps independently, instead of dragging every consumer
through a lockstep version. Within the group, a version moves only for a breaking
change: new optional fields are additive and stay `v1alpha1`.

Either half of the pair not matching skips the document with a diagnostic; unknown
*fields* within a known version are ignored with a diagnostic, so an old launchpad
renders a newer chip's basics. The payload is deliberately CRD-shaped: if the contract
ever graduates to a CRD with admission-time validation, re-homing the document into a CR
spec is a packaging change, not a schema change.

### Chip schema (`v1alpha1`)

| Field | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | `[a-z0-9-]{1,63}` | yes | — | diagnostic identity and render key; duplicate ids within one document reject the later chip |
| `title` | string ≤ 60 | yes | — | rendered as text; longer truncates with a diagnostic |
| `description` | string ≤ 200 | no | `""` | |
| `icon` | name from the bundled registry | no | `app` | unknown name → default + diagnostic |
| `iconData` | `data:image/(png\|jpeg\|svg+xml);base64,…` ≤ 16 KiB | no | — | wins over `icon`; rendered via `<img>` only |
| `tone` | `indigo emerald amber violet rose cyan red orange slate` | no | `indigo` | names map to bundled light+dark class sets; unknown → default + diagnostic |
| `link.subdomain` | DNS label | see link note | — | resolved server-side against the request host |
| `link.path` | string starting `/` | see link note | `""` | suffix on `subdomain`, or alone as a same-origin link (deep links such as `/auth/sso`) |
| `link.url` | absolute `http(s)` URL | see link note | — | external destinations; excludes the other two |
| `newTab` | bool | no | `true` | |
| `group` | group id | no | `more` | unknown id → a group is synthesized from the id (title-cased, `cards` layout, weight 500) + diagnostic |
| `weight` | number | no | `100` | lower renders first; ties break by title, then id |
| `audience` | `user` \| `admin` | no | `user` | `user` = any authenticated user |
| `enabled` | bool | no | `true` | lets a chart gate a chip from its values without deleting the ConfigMap |

**Link note.** A chip's link must resolve to exactly one destination, in one of three
shapes: `subdomain` (optionally carrying `path` as a suffix), a rooted `path` alone
(same-origin), or an absolute `url`. `url` excludes the other two fields. A chip with
no valid destination or no valid `title` is skipped — the minimum viable chip is a
title and somewhere to go.

Subdomain links resolve against the request's `X-Forwarded-Host`/`Host` header (what
Traefik forwards is what the browser sees), falling back to the `NEXTAUTH_URL` host —
so chip authors state a subdomain, never a hostname.

### Group schema (`v1alpha1`)

| Field | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | `[a-z0-9-]{1,63}` | yes | — | chips reference this |
| `title` | string ≤ 60 | yes | — | section heading |
| `description` | string ≤ 200 | no | `""` | section subtitle |
| `icon` | registry name | no | `folder` | small header icon |
| `weight` | number | no | `100` | section order; Scout's own groups ship at 10/20/30 so others can slot anywhere |
| `layout` | `cards` \| `rows` \| `tiles` | no | `cards` | large cards / compact list / compact 2-col tiles |
| `maxColumns` | 1–4 | no | `cards` 3, `tiles` 2 | rendered columns = `min(visibleChips, maxColumns)` |
| `width` | `full` \| `half` | no | `full` | consecutive visible half-width groups pair side-by-side; an unpaired half renders full |
| `audience` | `user` \| `admin` | no | `user` | independent of chip audience |
| `footerLink` | `{text, url}` | no | — | an inline link rendered under the group's grid |

A group with zero visible chips does not render — this one rule replaces per-section
enable flags and admin-section special-casing. Any document may define groups; if
several documents define the same group id, the launchpad's own mounted catalog wins,
then lowest weight, then lexicographic source, with a diagnostic. Chips union into the
group from all sources regardless.

### Discovery: sidecar file-sync

A `kiwigrid/k8s-sidecar` container (the same image the Grafana chart embeds) runs
beside the launchpad, watching ConfigMaps with the catalog label across **all
namespaces** (`NAMESPACE=ALL` — decided deliberately: zero-config for new components
and consistent with where the Grafana sidecar configuration is heading; a site wanting
a tighter posture can scope the namespace list). It materializes each data key as a
file in a shared `emptyDir` with `UNIQUE_FILENAMES=true` (every chart will plausibly
name its key `apps.yaml`), removes files when ConfigMaps are deleted or unlabelled, and
recycles its watch on a short timeout upstream precisely because long-lived Kubernetes
watches die silently. Its image version is pinned in `versions.yaml` with a
`# renovate:` annotation (ADR 0015), and its health endpoint gets a liveness probe.

This keeps the launchpad free of any Kubernetes client: its input is a directory of
YAML files, which is also the local-development story (point the loader at a fixtures
directory; no cluster required). The RBAC to support it — get/list/watch on ConfigMaps,
cluster-wide — is authored explicitly in the launchpad chart as a
ClusterRole/ClusterRoleBinding on the pod's ServiceAccount. This is the first
explicitly-authored RBAC object in the repo (Grafana's equivalent grant comes from its
upstream chart's defaults), which is a feature: the grant is visible and reviewable.
The cluster-wide read of ConfigMaps — objects that can hold more than chips — is the
accepted security delta of this design.

**Availability floor.** The launchpad's *own* chips (core services, admin tools) travel
the same schema but are delivered by a chart-rendered ConfigMap **mounted directly into
the pod** rather than discovered — the kubelet guarantees it exists before the app
starts. If discovery breaks, the floor is "core page, optional chips stale or missing,"
never an empty page; files already written by the sidecar persist, so discovered chips
go stale rather than absent.

### Staging and rendering

The data has four homes, each with one job: the **ConfigMaps** (etcd) are the source of
truth; the **emptyDir** is the app-facing staging area, rebuilt on pod start and
converged by the watch; an **in-process snapshot** holds the parsed, validated,
normalized catalog plus its diagnostics — rebuilt only when a cheap directory signature
changes, checked at most once per short TTL (seconds), serving the previous snapshot if
a rebuild fails; and **per-request assembly** filters by the session's audience,
resolves links against the request host, and lays out sections — pure computation,
microseconds at any plausible catalog size. Nothing on the request path touches the
Kubernetes API, and no external cache is involved: the catalog is kilobytes, already
per-pod, already durable in etcd — a shared cache would add a failure mode to solve a
problem this page does not have.

### Validation and graded degradation

One bad config must never blank the front door. The error budget is graded and
enforced in the validation layer (zod schemas over documents parsed with a
collect-don't-throw YAML parser):

- **Bad field → costs the field.** Presentation fields (icon, tone, description,
  overlong title) fall back to defaults with a diagnostic; the chip still renders.
- **Bad chip → costs the chip.** Below the minimum (valid title + valid destination)
  the chip is skipped with a diagnostic naming its source and reason.
- **Bad document → costs its chips.** YAML syntax errors and unknown `apiVersion`
  lose that document only; sibling documents and the page are untouched.
- **Security validation is strict, not lenient.** Destinations admit only
  `http(s)` URLs, rooted paths, and DNS-label subdomains — `javascript:` or `data:`
  links reject the chip. `iconData` must match the allowlisted image data-URI prefix
  and size cap and renders only via `<img src>`, never inlined into the DOM. All
  strings render as text nodes; nothing is ever interpolated as HTML. Catalog strings
  originate in other namespaces and are untrusted by definition.

Diagnostics are part of the product: every skip and coercion carries its source
(`namespace/name/key`, chip id) and reason, goes to the logs (hence Loki), and surfaces
as an admin-only note on the page itself — the person who just shipped a broken chip is
looking at the launchpad, not at Loki. A React error boundary per section is the last
line, so a renderer bug costs a section, not the tree.

### Roles and visibility

v1 visibility is `audience: user | admin`, filtered **server-side** from the session,
so a non-admin's HTML never contains admin chips. Coarseness is deliberate: the
launchpad's `groups` claim carries *client roles* (via ~~the `microprofile-jwt` mapper~~
a client-role mapper on the launchpad client, pinned to that client), and the launchpad
client sets `fullScopeAllowed: false` admitting exactly `launchpad-admin` and
`launchpad-user` to keep the session cookie under size limits. A plugin's own client
roles never reach this token, so per-chip gating cannot key on them. If finer gating
is ever needed, the compatible path is additional
launchpad-owned client roles surfaced as a `requiredRole` field — deferred until a real
chip needs it. At any grain, visibility resolves at login (stale up to the session
lifetime after a Keycloak change, like the existing admin flag), and chip visibility is
**UX, not authorization**: every target service keeps enforcing its own access at its
own edge (ADR 0003).

### Icons and tones

Named icons resolve through a curated name → component map over the react-icons sets
already bundled in the image — rendered as inline SVG (no network, no `img-src`
involvement, tree-shaking preserved; a dynamic lookup over entire icon sets would put
megabytes in the bundle). Tones are a closed palette of coordinated light+dark Tailwind
class bundles (Tailwind requires build-time class enumeration, and a raw hex value
cannot buy the dark-mode pairing). Authors needing a logo the registry lacks embed it
as `iconData`. No remote icon URLs exist in the contract at all, satisfying ADR 0012
and the air-gap posture by construction.

### Scout's own chips ride the same contract

Each owning role/chart ships its chips as labelled ConfigMaps: open-webui ships Chat,
voila ships the Playbooks group and rows, MinIO ships Lake, Temporal ships
Orchestrator, Grafana ships Monitor. The launchpad's chart keeps only the core group
(Analytics, Notebooks, the docs footer link) and the admin group (Users) in its mounted
catalog. The `ENABLE_CHAT` / `ENABLE_PLAYBOOKS` / `ENABLE_MINIO` environment variables
are retired from the launchpad deployment, chart, and role — the inventory `enable_*`
flags remain as deployment gates for the components themselves, and the page follows
installation. Scout's components thereby become the reference implementations of the
contract, exercised by many owners from day one.

## Consequences

- Putting a service on the landing page = one labelled ConfigMap in the service's own
  chart or role; no launchpad rebuild, no launchpad edit. Removal is deletion.
- The launchpad stops knowing what is on the page: presence bugs become config bugs
  with per-chip diagnostics instead of image rebuilds.
- The three `ENABLE_*` env vars and their plumbing disappear; the launchpad role stops
  passing content flags. The page reflects what is installed.
- New moving parts: one sidecar container (pinned + Renovate-watched, ADR 0015), zod
  and yaml as launchpad dependencies, and the repo's first explicitly-authored
  ClusterRole (ConfigMap get/list/watch, cluster-wide, attached to the launchpad pod's
  ServiceAccount). The token is projected only into the sidecar, so the app container
  holds no API credentials; the cost is that the pod hand-rolls a volume the kubelet
  would otherwise supply.
- Any workload that can create a labelled ConfigMap in any namespace can put a chip on
  the front door. Mitigations: strict destination/icon validation, text-only
  rendering, per-chip degradation, admin-visible diagnostics. The residual
  namespace-trust question is accepted for now and revisited if tenancy tightens.
- The authoring contract is the docs page plus the zod validator itself; the page's
  icon and tone galleries are generated from the launchpad's own registries, so what
  the docs show and what the page renders cannot disagree.
- The launchpad gains test infrastructure (the catalog module is pure functions over
  fixtures) and loses the client-side URL-resolution skeleton: links resolve
  server-side from the request host, so first paint gets faster, and role filtering
  moves server-side with it.
- Failure floor: with discovery broken, the mounted core catalog still renders; with a
  malformed catalog, the page renders everything valid and reports the rest.

## Alternatives Considered

| Option | Verdict |
| --- | --- |
| **Labelled ConfigMaps + sidecar file-sync (selected)** | Decentralized publish, app stays Kubernetes-free, failure degrades to staleness, pattern already proven in-cluster by Grafana |
| In-process Kubernetes reads (`@kubernetes/client-node`) | Rejected: the informer API has no stability level, no auto-reconnect, and documented silent-stall issues; the honest variant is periodic LIST, which puts a Kubernetes dependency and poll lifecycle inside the app to achieve what the sidecar does with none of our code |
| CRD + controller/operator | Deferred: admission-time validation and `kubectl get` UX are attractive, but a CRD lifecycle plus controller is disproportionate for display metadata; the document is kept CRD-shaped so graduation later is packaging, not schema |
| Ingress/route-annotation discovery (Forecastle/Hajimari style) | Rejected: chips ≠ routes — deep links share one subdomain, some chips have no route, some routes deserve no chip — and flat annotation strings cannot carry groups, tones, or audiences |
| Central values-rendered ConfigMap only | Rejected as *the* mechanism (recentralizes registration into launchpad config); retained as the delivery detail for the launchpad's own core chips, where central is correct and the kubelet-guaranteed mount is the availability floor |
| Shared cache (Valkey) for the catalog | Rejected: kilobytes of data already replicated per-pod by the sidecar and durable in etcd; a cache tier adds failure modes to buy cross-replica agreement a landing page does not need |
