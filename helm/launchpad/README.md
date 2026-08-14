# launchpad

Scout's landing page. Beyond the Next.js app itself, this chart wires the runtime
catalog that fills the page (ADR 0034):

- **`templates/configmap-catalog.yaml`** — the launchpad's *own* chips (core/admin
  section definitions, Analytics, Notebooks, Users), mounted directly into the pod at
  `/app/config/catalog`. The direct mount is the availability floor: the kubelet
  guarantees it exists, so the core page renders even when discovery is broken.
- **A kiwigrid/k8s-sidecar container** (`catalog.discovery.*` values) — watches
  ConfigMaps labelled `launchpad.scout.xnat.org/catalog: "true"` across namespaces and
  materializes their data keys into an emptyDir at `/app/config/discovered`. This is
  how every other component's chips arrive; nothing else on the page is configured
  here.
- **`templates/rbac.yaml`** — the ClusterRole/ClusterRoleBinding (ConfigMaps
  get/list/watch, cluster-wide) the sidecar needs, attached to the pod's
  ServiceAccount. Authored explicitly so the grant stays visible; scope it down by
  setting `catalog.discovery.namespace` to a comma-separated list instead of `ALL`.

The app reads the directories named in `LAUNCHPAD_CATALOG_DIRS` (set by
`templates/deployment.yaml`; earlier directories win group-definition conflicts),
revalidating on a ~10 s cadence — catalog changes propagate without a pod restart.

To put a service on the page, do not edit this chart: ship a labelled ConfigMap with
that service. Authoring guide and schema:
`docs/source/customize/launchpad-chips.md`.

Everything else (`auth.*`, ingress, favicon route, image, probes) is conventional and
documented inline in `values.yaml`.
