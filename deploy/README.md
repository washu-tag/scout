# `deploy/` — Scout GitOps deployment base (ADR 0031, Phase 3)

Kustomize bases + Flux `Kustomization`s that stand Scout up by *pulling* signed,
digest-pinned artifacts instead of the Ansible push. WIP scaffold: the ingest
vertical slice first (postgres -> lake -> orchestrator -> extractor), unconsumed
until CI switches `deploy-and-test` to deploy from it. See
`docs/internal/gitops-implementation-plan.md` and ADRs 0030 / 0031.

## Layout
- `base/<component>/{operator,cluster,...}/` — Kustomize bases (the k8s resources).
  CRD-owning operators split into `operator/` (install) and the CR (`cluster/`),
  so a CR never dry-runs before its CRD exists.
- `flux/<component>.yaml` — the Flux `Kustomization` CRs pointing at the bases and
  wiring the DAG (`dependsOn` + CEL `healthChecks`), reproducing the Ansible order.
- `base/edge-{on-prem,aws}/` + `base/storage-ready/` — per-mode resources (ADR 0035):
  the ingress/auth edge (on-prem Traefik forwardAuth Middlewares vs aws ALB-native-OIDC
  Ingresses) and the inert aws storage marker. Wired by `modes/{on-prem,aws}/`, not the
  shared DAG.
- `modes/{on-prem,aws}/` — the per-mode Flux set, a sibling of `flux/` (not nested under
  it, so `flux/` has no subdir to recurse into): the `storage-ready` gate (inert in aws;
  the real MinIO tenant on-prem), the ingress edge, and on-prem MinIO + oauth2-proxy.
  `flux/` holds only the shared set, so a site reconciles it plus exactly one mode via a
  Kustomization pointing at `./modes/${service_mode}`. The lake consumers dependsOn the
  mode-agnostic `storage-ready` name, supplied by whichever mode the site selects.

## Conventions
- **Site scalars are `${var}` postBuild substitutions** from a `cluster-vars`
  ConfigMap (namespaces, storage classes/sizes, endpoints). The kustomize-controller
  runs with `StrictPostBuildSubstitutions`, so an undefined `${var}` fails the
  build rather than rendering empty.
- **Chart/image refs** are stamped from the build-lane haul at config-artifact
  publish (placeholder in git, concrete only in the published artifact). Upstream
  chart versions are pinned in `versions.yaml` + Renovate-tracked.
- **Secrets by fixed name only** — bases reference them (e.g. `superuser-secret`);
  values are seeded by CI/site (Phase 3) or SOPS/ESO (Phase 4), never in git. The
  full contract (names, keys, per-mode materialization) is in `required-secrets.md`.
- **Service-mode (`aws` vs `on-prem`, ADR 0035) picks a mechanism by the shape of the
  delta**, so the three-way split is one rule, not ad hoc:
  1. *scalar diff* → an inline `${var}` the chart branches on (e.g. hive
     `S3_PATH_STYLE_ACCESS`, the extractor `sparkDefaults.mode`).
  2. *list-membership / block diff a scalar can't express* (envFrom entries, catalog
     lines, an aws-only ServiceAccount) → a per-mode `valuesFrom` edge ConfigMap named
     `<workload>-edge-${service_mode}` (trino, extractor, opa, superset).
  3. *a whole resource present in one mode only, or a CRD the other mode lacks*
     (MinIO, the Traefik Middlewares, oauth2-proxy, the ALB Ingresses) → the mode
     set `modes/{aws,on-prem}/`, since `${var}` can't add/drop a document and a flux
     path isn't substituted.

## Status
**Bases + DAG done for the ingest slice + the auth/analytics layer** (22 Flux
`Kustomization`s, acyclic): postgres, minio, cassandra, elasticsearch, hive,
temporal, extractor, valkey, keycloak (+ realm), oauth2-proxy, opa, trino (ro+rw),
superset (+ dashboards).

Remaining components: jupyter, report-viewer, monitoring, launchpad, and the
feature Components (chat/voila/xnat/data-generator/gpu).

Pre-deploy fixes (deferred; all gated on the build lane being live, which is where
Scout-chart versions get stamped):
1. **Per-namespace foundation base**: several `prune: true` Kustomizations
   co-declare the same `Namespace` *and* the same `scout-charts` / upstream
   `HelmRepository` in a shared namespace. This is wider than it looks: Ansible's
   per-component namespace vars (`hive_namespace`, `extractor_namespace`,
   `trino_namespace`, ...) alias down to a handful of real namespaces
   (scout-extractor, scout-analytics, ...), so once `cluster-vars` maps them
   faithfully many more bases resolve to the same `Namespace` than a
   pre-substitution scan shows. Two Kustomizations owning one `Namespace` means a
   prune in either cascades-deletes it (and everything in it) out from under the
   other. Fix: a `base/<namespace>/foundation/` per real namespace that declares
   the `Namespace` + shared `HelmRepository`s once, owned by one foundational
   Kustomization every component in that namespace `dependsOn`; component bases set
   `namespace:` and drop those objects. Needs the Ansible namespace-default map to
   collapse the aliased vars correctly.
2. **Config-artifact publish job** stamps the Scout charts' `0.0.0` placeholders
   with real published versions (from the haul).
3. **`deploy-and-test` switch** to deploy the ingest slice via Flux (ingest suite
   = gate).
