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
**Ingest vertical slice bases + DAG done**: postgres, minio, cassandra,
elasticsearch, hive, temporal, extractor (13 Flux `Kustomization`s, acyclic,
operator/CR splits with health-gated edges). Next: the config-artifact publish
job (stamp refs from the haul + emit the `cluster-vars` ConfigMap + `required-vars`)
and the `deploy-and-test` switch (the ingest suite is the gate).
