# ADR 0035: Cloud vs On-Prem Service Mode for the GitOps Deploy Base

**Date**: 2026-08
**Status**: Proposed. Both the storage/identity and ingress/auth edges are
implemented on branch `feat/deploy-service-mode`.
**Decision Owner**: TAG Team

## Context

ADR 0011 established a **service-mode** (`aws` vs `on-prem`) layered architecture and
called out the two edges that differ by platform: **storage/identity** (local-path +
MinIO + access keys on-prem vs S3 + IRSA on cloud) and **ingress** (Traefik Middleware
CRDs on-prem vs AWS ALB). The Scout charts already carry parts of that mode: IRSA
`serviceaccount.yaml` templates and hl7-transformer's mode-switched `spark-defaults`
`_helpers.tpl` (ADR 0031 phase 0).

The Phase-3 `deploy/` base and the `scout-config` OCI artifact (ADR 0031), however, were
**hardcoded to on-prem**: hive/metastore pinned `S3_PATH_STYLE_ACCESS=true` + a MinIO
endpoint; the extractor pinned chart-owned `spark-defaults` `mode: on-prem`; trino ro/rw
carried a MinIO catalog with static access keys; ingress everywhere is Traefik.

The first real consumer is **adapt-dev**, a cloud (EKS Auto) cluster with no Traefik (it
uses ALB) whose Scout data lake is on AWS S3 + IRSA. Other cloud setups will be the same.
A per-site ALB/S3 overlay would make every cloud site re-implement the edge, the
hand-ported drift ADR 0031 exists to kill. So the **artifact itself must express both
modes** and let a site select one with a single value, extending ADR 0011's service-mode
down into the pulled config artifact.

## Decision

A single **`service_mode` cluster-var (`aws` | `on-prem`)**, resolved per site like every
other `${var}`. How each mode delta is expressed depends on its shape:

1. **Value-class deltas: inline `${var}`, the chart branches.** Where the difference is a
   scalar the chart already conditions on, it is a plain cluster-var in the HelmRelease
   values and the chart does the conditional. envsubst just passes the string, no extra
   machinery. Applied to hive (`S3_PATH_STYLE_ACCESS`, `HADOOP_OPTS`, and the IRSA
   `serviceAccount.annotations` role-arn, inert off-EKS) and the extractor's chart-owned
   `sparkDefaults.mode: '${service_mode}'` (the chart then emits the WebIdentity provider
   + virtual-host S3 in aws).

2. **Config-block deltas: a per-mode edge ConfigMap, selected by name via `valuesFrom`.**
   Where the difference is conditional *lines or blocks* a scalar cannot express, list
   membership (`envFrom`), present/absent properties (the Trino catalog's access-key
   lines), or two shapes of one key (`hostPath` vs `emptyDir` volumes), the HelmRelease
   moves those blocks out of `spec.values` into a pair of ConfigMaps shipped side by side,
   `<workload>-edge-aws` and `<workload>-edge-on-prem`, and selects one with
   `valuesFrom: [{kind: ConfigMap, name: '<workload>-edge-${service_mode}'}]`. Both ship
   in the shared base; `service_mode` picks one by name and the other is inert. Flux
   merges the ConfigMap under `spec.values`, so the base keeps only mode-invariant values
   inline. Applied to trino (ro + rw: catalog + `serviceAccount` + `envFrom`) and both
   extractor workers (`envFrom` + `volumes`/`volumeMounts` + the aws `serviceAccount`).

3. **Structural / raw-manifest deltas: per-mode edge sets under `base/edge-{on-prem,aws}/`.**
   The auth/ingress edge is raw manifests a ConfigMap `valuesFrom` cannot reach, so it
   ships as two kustomize dirs a site selects between (a `scout-edge` Kustomization ->
   `./base/edge-${service_mode}`), not in the shared `flux/` DAG. on-prem carries the
   oauth2-proxy Traefik forwardAuth `Middleware`s, relocated out of `base/oauth2-proxy`
   because they are Traefik CRDs an aws cluster has no controller for (the one required
   base change; everything else on-prem is a standard Ingress that is simply inert in
   aws). aws carries public ALB Ingresses with ALB-native OIDC, mirroring the live
   adapt-dev pattern: the ALB reuses the same `oauth2-proxy` Keycloak client (via the
   `alb-oidc-keycloak` Secret), so the realm's `oauth2-proxy-user` approval gate still
   applies, and Keycloak is exposed un-gated (it is the OP) with its master-realm admin
   paths blocked by an ALB fixed-response.

**MinIO is not an edge.** Earlier framing had the in-cluster MinIO Tenant as on-prem-only,
with the open question of how storage consumers' `dependsOn: minio-tenant` survives in
aws. Implementation resolved it: the MinIO Tenant is **mode-independent**. It backs the
OPA authz-bundle pipe (the Keycloak SPI writes the bundle, OPA polls it) and the bundle
service accounts in *both* modes, decoupled from the data lake. So `minio-tenant` ships
unconditionally, the shared `dependsOn` holds in aws, and only the data-lake *access*
(hive/trino/extractor) flips to S3 + IRSA. No no-op tenant, no mode-specific
storage-readiness edge.

**opa stays on MinIO.** opa is a HelmRelease and S3-adjacent, but its bundle is an
internal control-plane artifact, not lake data, and its chart has no ServiceAccount
template (IRSA is not expressible without a chart change). opa and its Keycloak-SPI writer
are left unflipped in both modes. Moving them to S3 would let an aws cluster drop MinIO
entirely, a separate decision needing a chart change + a dedicated bundle-reader role.

**The secret contract is mode-specific** and documented in `required-secrets.md`: cloud
uses IRSA (no object-store access-key Secrets), on-prem uses the MinIO-user credential
Secrets. `service_mode` and the edge together determine which secrets a site provisions.

The shared DAG (postgres, keycloak, cassandra, elasticsearch, temporal, valkey, the
analytics apps) stays mode-agnostic; only the edge moves.

## Alternatives considered

- **Chart-internal branching on `service_mode` for the config-block deltas** (pass the
  mode as a value; the chart conditions the catalog/envFrom/volumes). Works for the
  value-class deltas (used above) but not the config-block ones: the Trino catalog's
  present/absent access-key lines and the hostPath-vs-emptyDir volume are structural, and
  baking a mode switch into every such chart spreads the conditional across many charts
  we do not all own. The `valuesFrom` edge keeps the conditional in one reviewable place
  per workload.
- **Per-mode flux paths for the HelmRelease config-blocks too** (as with the ingress
  edge). Rejected for values: a single `service_mode` var selects a ConfigMap by name
  inside the shared DAG, so there is no need for a site to reconcile a different flux path
  or for a mode-named `dependsOn`. Flux-path selection is reserved for the genuinely raw
  manifests (ingress) that `valuesFrom` cannot reach.
- **Kustomize Components** (`components/storage-s3`, `components/ingress-alb`). Rejected
  for this consumption model: a Component must be *included* by a `kustomization.yaml`,
  but the artifact ships the kustomizations and a site only pulls the artifact and selects
  which `flux/` paths to reconcile, it cannot inject a Component.
- **Per-site ALB/S3 overlay** (Flux `spec.patches` in each site repo). Rejected: every
  cloud site re-implements and maintains the same edge patch, the hand-ported drift ADR
  0031 removes. The edge belongs in the shared artifact.
- **A second, aws-only base.** Rejected: duplicates the entire DAG for a difference
  confined to the edge.

## Consequences

- One `scout-config` artifact ships both modes; a site is a `service_mode` value (+ one
  ingress edge set, once that lands). adapt-dev is the first `aws`-mode consumer, set via a
  gitops change (its cluster-vars + IRSA/ESO secrets), not in this repo.
- The **storage edge is implemented** (hive value-class; trino + extractor config-block).
  `service_mode`, `lake_reader_role_arn`, `lake_writer_role_arn`, `s3_path_style_access`,
  and `hive_hadoop_opts` join the `required-vars` contract; `validate-deploy` renders both
  modes so neither rots (only one is exercised on any given cluster).
- Completing the `aws` branch was **real per-chart work, not a values toggle**, and it
  surfaced two latent bugs in the Ansible `aws_deployment` path that were fixed rather than
  ported: trino-ro dropped the mode-independent `trino-authz-env` (keystore +
  internal-comm) alongside the S3 creds, and the extractor never wired an IRSA
  ServiceAccount at all (aws lake writes would fail). **Do not port the Ansible aws branch
  verbatim.**
- `minio-tenant` is mode-independent (resolved); opa + the Keycloak SPI writer are
  deliberately left on MinIO. Both are revisitable if an aws cluster ever needs to drop
  MinIO, gated on opa chart SA support + a bundle-reader role.
- The **ingress edge is implemented** as `base/edge-{on-prem,aws}/`: on-prem Traefik
  forwardAuth Middlewares vs aws ALB-native-OIDC Ingresses (Keycloak un-gated + an admin
  fixed-response; Superset ALB-OIDC). Adds `acm_cert_arn` + `alb_group_name` to the
  contract and `alb-oidc-keycloak` (the ALB's copy of the oauth2-proxy client creds) to
  the site-seeded secrets; scheme comes from the `alb`/`alb-internal` IngressClassParams
  (Layer-0, which override the per-ingress annotation on EKS Auto). Only Superset +
  Keycloak are covered so far (the base's public components); jupyter, launchpad, and
  monitoring follow as those components land in the base.

## Related

- ADR 0011 (service-mode layered architecture), extended here to the deploy base.
- ADR 0031 (GitOps deployment base), the base + artifact this parameterizes.
- ADR 0030 (two-lane versioning), the artifact publish lane that ships both edges.
- `deploy/required-secrets.md`, `deploy/required-vars.txt`, the per-mode contracts.
