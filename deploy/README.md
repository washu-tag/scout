# `deploy/` — Scout GitOps deployment base (ADR 0031, Phase 3)

Kustomize bases + Flux `Kustomization`s that stand Scout up by *pulling* signed,
digest-pinned artifacts instead of the Ansible push. WIP scaffold: the ingest
vertical slice first (postgres -> lake -> orchestrator -> extractor), unconsumed
until CI switches `deploy-and-test` to deploy from it. See
`docs/internal/phase3-implementation-plan.md`.

## Layout
- `base/<component>/{operator,cluster,...}/` — Kustomize bases (the k8s resources).
  CRD-owning operators split into `operator/` (install) and the CR (`cluster/`),
  so a CR never dry-runs before its CRD exists.
- `flux/<component>.yaml` — the Flux `Kustomization` CRs pointing at the bases and
  wiring the DAG (`dependsOn` + CEL `healthChecks`), reproducing the Ansible order.

## Conventions
- **Site scalars are `${var}` postBuild substitutions** from a `cluster-vars`
  ConfigMap (namespaces, storage classes/sizes, endpoints). The kustomize-controller
  runs with `StrictPostBuildSubstitutions`, so an undefined `${var}` fails the
  build rather than rendering empty.
- **Chart/image refs** are stamped from the build-lane haul at config-artifact
  publish (placeholder in git, concrete only in the published artifact). Upstream
  chart versions are pinned in `versions.yaml` + Renovate-tracked.
- **Secrets by fixed name only** — bases reference them (e.g. `superuser-secret`);
  values are seeded by CI/site (Phase 3) or SOPS/ESO (Phase 4), never in git.

## Status
**Bases + DAG done for the ingest slice + the auth/analytics layer** (22 Flux
`Kustomization`s, acyclic): postgres, minio, cassandra, elasticsearch, hive,
temporal, extractor, valkey, keycloak (+ realm), oauth2-proxy, opa, trino (ro+rw),
superset (+ dashboards).

Remaining components: jupyter, report-viewer, monitoring, launchpad, and the
feature Components (chat/voila/xnat/data-generator/gpu).

Pre-deploy fixes (deferred; all gated on the build lane being live, which is where
Scout-chart versions get stamped):
1. **Shared sources**: several `prune: true` Kustomizations co-declare the same
   `scout-charts` (and upstream) `HelmRepository` in a shared namespace (e.g.
   scout-analytics: opa, superset, trino) and would fight over it. Extract the
   `HelmRepository` declarations into a per-namespace `sources` base owned by one
   Kustomization.
2. **Config-artifact publish job** stamps the Scout charts' `0.0.0` placeholders
   with real published versions (from the haul).
3. **`deploy-and-test` switch** to deploy the ingest slice via Flux (ingest suite
   = gate).
