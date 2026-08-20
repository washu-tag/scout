# ADR 0035: Cloud vs On-Prem Edge Mode for the GitOps Deploy Base

**Date**: 2026-08
**Status**: Proposed
**Decision Owner**: TAG Team

## Context

ADR 0011 established a **service-mode** (`aws` vs `on-prem`) layered architecture and
called out the two edges that differ by platform: **ingress** (Traefik Middleware CRDs
on-prem vs AWS ALB) and **storage/identity** (local-path + MinIO + access keys on-prem
vs EBS/EFS + S3 + IRSA on cloud). The Scout charts carry that mode today: IRSA
`serviceaccount.yaml` templates and hl7-transformer's mode-switched `spark-defaults`
`_helpers.tpl` (ADR 0031 phase 0).

The Phase-3 `deploy/` base and the `scout-config` OCI artifact (ADR 0031), however, are
**hardcoded to on-prem**. This is deliberate and documented in the base:

- `hive/metastore`: "On-prem path (S3_PATH_STYLE_ACCESS=true, MinIO endpoint); the
  `aws_deployment` IRSA ServiceAccount + WebIdentity branch is intentionally omitted."
- `extractor`: chart-owned `spark-defaults` pinned `mode: on-prem`.
- `trino` ro/rw: "On-prem: MinIO S3."
- ingress everywhere is Traefik (`ingressClassName: traefik` + `Middleware` forwardAuth).

The first real consumer is **adapt-dev**, a cloud (EKS Auto) cluster that runs no MinIO
(its Scout uses AWS S3 + IRSA) and no Traefik (it uses ALB). Other cloud setups will be
the same. A per-site ALB/S3 overlay would make every cloud site re-implement the edge,
the hand-ported drift ADR 0031 exists to kill. So the **artifact itself must express
both modes** and let a site select one, extending ADR 0011's service-mode down into the
pulled config artifact.

## Decision

Parameterize the deploy-base edge in two layers, most of it as a value the charts
already branch on, and only the genuinely structural deltas as separate flux paths.

1. **A `service_mode` cluster-var (`aws` | `on-prem`)**, resolved per site like every
   other `${var}`, flowed into the HelmRelease values. The charts branch internally on
   it (IRSA ServiceAccount + WebIdentity vs access-key `envFrom`; virtual-host vs
   path-style S3; `ingressClassName` + annotations). envsubst passes the string; the
   chart does the conditional. This reuses ADR 0011's mode instead of duplicating the
   DAG. Where the base intentionally omitted the aws branch (hive IRSA, the trino
   catalogs, the spark-defaults `aws` path), that branch is completed as part of this
   work, chart by chart.

2. **A minimal `flux/edge-cloud/` and `flux/edge-airgapped/` set** for the deltas a
   string substitution cannot express:
   - the in-cluster **MinIO Tenant** and its bucket/user bootstrap (on-prem only;
     cloud omits it entirely and uses real S3);
   - the **auth/ingress edge** that is raw manifests, not chart values: the Traefik
     `Middleware` forwardAuth + security-headers (on-prem) vs ALB-OIDC ingress
     annotations (cloud), and the raw Keycloak `Ingress`.
   A site applies the shared, mode-agnostic DAG (`flux/`) plus exactly one of the two
   edge sets. This is a handful of resources, not a second stack.

3. **The secret contract is mode-specific** and already documented in
   `required-secrets.md`: cloud uses IRSA (no object-store access-key Secrets), on-prem
   uses the MinIO-user credential Secrets. The `service_mode` var and the edge set
   together determine which secrets a site provisions.

The shared DAG (postgres, keycloak, cassandra, elasticsearch, temporal, valkey, the
extractor and analytics apps) stays mode-agnostic; only the edge moves.

## Alternatives considered

- **Kustomize Components** (`components/ingress-alb`, `components/storage-s3`). Rejected
  for this consumption model: a Component must be *included* by a `kustomization.yaml`,
  but the artifact ships the kustomizations and a site only pulls the artifact and
  selects which `flux/` paths to reconcile, it cannot inject a Component. Per-mode flux
  paths are site-selectable; Components are not.
- **Per-site ALB/S3 overlay** (Flux `spec.patches` in each site repo). Rejected: every
  cloud site re-implements and maintains the same edge patch, which is exactly the
  hand-ported drift ADR 0031 removes. The edge belongs in the shared artifact.
- **A second, aws-only base.** Rejected: duplicates the entire DAG and doubles the
  maintenance surface for a difference confined to the edge.

## Consequences

- One `scout-config` artifact ships both modes; a site is a `service_mode` value + one
  edge set. adapt-dev is the first `aws`-mode consumer.
- Completing the `aws` branch across the S3-touching charts (hive, trino, extractor,
  opa) and the ALB ingress edge is real, chart-by-chart work; this ADR scopes it, a
  follow-up implements it.
- `service_mode` joins the `required-vars` contract; `validate-deploy` must render both
  modes so neither edge rots (only one is exercised on any given cluster).
- The DAG stays mostly mode-agnostic, so future components are added once, not twice.

## Related

- ADR 0011 (service-mode layered architecture) — extended here to the deploy base.
- ADR 0031 (GitOps deployment base) — the base + artifact this parameterizes.
- ADR 0030 (two-lane versioning) — the artifact publish lane that ships both edges.
- `deploy/required-secrets.md`, `deploy/required-vars.txt` — the per-mode contracts.
