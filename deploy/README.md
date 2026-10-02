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
- `base/secrets-on-prem/` + `base/secrets-ready/`: per-mode too, the on-prem Secret
  templates and the inert aws secrets marker.
- `modes/{on-prem,aws}/` — the per-mode Flux set, a sibling of `flux/` (not nested under
  it, so `flux/` has no subdir to recurse into): the `secrets-ready` gate (inert in aws;
  the Secret templates on-prem), the `storage-ready` gate (inert in aws; the real MinIO
  tenant on-prem), the ingress edge, and on-prem MinIO + oauth2-proxy.
  `flux/` holds only the shared set, so a site reconciles it plus exactly one mode via a
  Kustomization pointing at `./modes/${service_mode}`. The lake consumers dependsOn the
  mode-agnostic `storage-ready` name, supplied by whichever mode the site selects.

## Conventions
- **Site scalars are `${var}` postBuild substitutions** from a `cluster-vars`
  ConfigMap (namespaces, storage classes/sizes, endpoints). Run the
  kustomize-controller with `StrictPostBuildSubstitutions` so an undefined `${var}`
  fails the build; without it the var renders empty. aws sites set `s3_sse_type` to
  `S3` when an SCP or bucket policy requires an SSE header on writes, else `NONE`
  (bucket default, which keeps an SSE-KMS default intact).
- **Chart/image refs** are stamped from the build-lane haul at config-artifact
  publish (placeholder in git, concrete only in the published artifact). Upstream
  chart versions are pinned in `versions.yaml` + Renovate-tracked.
- **Secrets by fixed name only** — bases reference them (e.g. `superuser-secret`);
  values are never in git. aws sites materialize them (External Secrets Operator).
  On-prem, `base/secrets-on-prem` renders them from one site `scout-secret-values`
  Secret whose keys are listed in `required-secret-values.txt`; each placeholder there is
  written `${sq}${value}${sq}`, because kustomize-controller re-serializes before it
  substitutes and would drop quotes written in the template. The full contract (names,
  keys, per-mode materialization, value rules) is in `required-secrets.md`.
- **Site realm values** go in an optional `keycloak-config-cli-site-values` ConfigMap
  (key `values.yaml`) in `${keycloak_namespace}`, which the keycloak-config-cli
  HelmRelease reads through `valuesFrom`. It carries realm chart values a scalar can't,
  such as a `trinoAttributeFilters` pick-list or extra IdP documents under `config`
  (`docs/internal/authentication.md`), whose credentials go in the optional
  `keycloak-client-secrets-site` Secret. The HelmRelease's own `values` win on overlap.
  Label it `reconcile.fluxcd.io/watch: Enabled` so an edit re-runs the import without
  waiting for the interval. With the label the import re-runs as soon as the ConfigMap
  changes, which can beat an ExternalSecret writing new `$(env:...)` keys in the same
  change, and one unresolved variable fails the whole import; add new keys first or
  skip the label.
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

## Site prerequisites (Layer 0)
Not in the artifact; a site provides them before reconciling it: cert-manager with the
`scout-internal-ca` ClusterIssuer, External Secrets Operator + a `ClusterSecretStore`
(cloud), and on aws the `alb` IngressClass/IngressClassParams plus the IRSA roles in
`required-secrets.md`. A site that seeds secrets from a Kustomization the artifact
`dependsOn` must pre-create the scout namespaces; the artifact's copies carry
`kustomize.toolkit.fluxcd.io/prune: disabled`, and so should the site's.

On-prem additionally needs Flux 2.9.6 or later with `StrictPostBuildSubstitutions`,
and the `scout-secret-values` Secret in `flux-system` before `secrets-ready` reconciles:
apply it from a site Kustomization (with `decryption` for SOPS) that the Kustomizations
reconciling `./flux` and `./modes/on-prem` dependsOn. Generate it with
`tooling/deploy/gen_secret_values.py`.

## Status
**Bases + DAG done for the ingest slice + the auth/analytics layer** (the shared
`Kustomization` DAG plus one per-mode set, acyclic): postgres, minio, hive, temporal
(on Postgres), extractor, valkey, keycloak (+ realm + fragment reconciler),
oauth2-proxy, opa, trino (ro+rw), superset (+ dashboards), launchpad.

Also shipped: launchpad, the aws ingress edge, and the on-prem Secret templates.
Remaining components: jupyter, report-viewer, monitoring, and the feature Components
(chat/voila/xnat/data-generator/gpu).

Done since the scaffold: the per-namespace foundation bases (`base/scout-*-foundation`,
one owner per Namespace + shared HelmRepository) and the config-artifact publish job
(stamps the Scout charts' `0.0.0` placeholders from the haul). Remaining: the
**`deploy-and-test` switch** to deploy the ingest slice via Flux (ingest suite = gate).
