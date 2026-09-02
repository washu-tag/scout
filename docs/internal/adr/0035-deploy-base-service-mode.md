# ADR 0035: Cloud vs On-Prem Service Mode for the GitOps Deploy Base

**Date**: 2026-08
**Status**: Accepted. Both the storage/identity and ingress/auth edges are
implemented on branch `feat/deploy-service-mode` (#679).
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

The first real consumer is a cloud (EKS Auto) cluster with no Traefik (it
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

3. **Structural / raw-manifest deltas: per-mode `modes/{aws,on-prem}/` sets.** The
   auth/ingress edge is raw manifests a ConfigMap `valuesFrom` cannot reach, so it ships in
   the mode set a site reconciles alongside the shared `flux/` DAG (the same place the
   `storage-ready` gate below lives; the base resources are under `base/edge-{on-prem,aws}/`).
   These live in `modes/` as siblings of `flux/`, not nested inside it, so the shared
   `path: ./flux` has no subdir to recurse into.
   on-prem carries the oauth2-proxy Traefik forwardAuth `Middleware`s, relocated out of
   `base/oauth2-proxy` because they are Traefik CRDs an aws cluster has no controller for.
   aws carries public ALB Ingresses with ALB-native OIDC. The ALB reuses the **oauth2-proxy
   Keycloak client** (each ALB host adds its `/oauth2/idpresponse` callback to that client's
   redirectUris), matching the live AWS realm. **Access is by realm membership**:
   ALB-native OIDC admits any user who completes the exchange and there is no per-role
   Keycloak gate, so authorization is the controlled realm membership (groups + brokered
   IdPs), the posture the live cluster already runs. (An earlier draft added a dedicated
   `alb-oidc` client bound to a browser-flow role gate; review found the `conditional-user-role`
   gate read the client role `oauth2-proxy-user` as a realm role and denied everyone, an
   existing SSO session short-circuited the CONDITIONAL execution, and the block was
   `aws_deployment`-gated so it never rendered into the artifact. Matching live is proven and
   simpler.) Keycloak itself is un-gated (it is the OP), master-realm admin paths blocked by
   an ALB fixed-response.

**Load-bearing assumption: the upstream OAuth apps are org-restricted.** With no per-role
gate, aws-mode authorization *is* realm membership, and the realm auto-creates a user on
first broker login (`registrationAllowed` is off, but both IdPs set `trustEmail: true` and
use the default first-broker-login flow, and nothing in the realm restricts which upstream
accounts may broker in). So the real boundary is the IdP / OAuth-app config, not the realm:
the Microsoft IdP is tenant-scoped (`tenantId`), and the GitHub OAuth app must be
org-restricted for GitHub sign-in to be a boundary at all. On-prem this was incidental (an
auto-created user still lacked `oauth2-proxy-user`, which oauth2-proxy's `allowed_roles`
enforced); aws has nothing in that position, so org-restriction of the upstream apps is
load-bearing. A site whose upstream apps are not org-restricted must add a gate before
exposing aws-mode ingress.

**Security response headers move app-side in aws.** On-prem chains ADR 0012's
`kube-system-security-headers` Traefik Middleware (CSP, HSTS, frame/content-type options)
onto every gated Ingress; ALB has no response-header injection, so that shared edge control
has no aws equivalent. In aws mode the headers are the application's responsibility: Superset
emits them via Flask-Talisman, Open WebUI carries its own CSP (ADR 0009), and report-viewer
plus the components that follow (jupyter, launchpad, monitoring) each set their own as they
land in the aws edge. Anything that can only get them from the Traefik middleware is an
explicit gap, not a silent one. A shared aws equivalent (a CloudFront response-headers-policy
or a WAF rule in front of the ALB) is possible later but out of scope here; the base does not
rely on an edge middleware for headers in aws. This is the ADR 0012 implication of dropping
Traefik that ADR 0011 did not weigh, and the reason the auth edge (oauth2-proxy) and the
hardening edge (security-headers) both need an aws answer, not just the former.

**MinIO is on-prem-only; aws ships none.** The lake consumers `dependsOn` a mode-agnostic
`storage-ready` Kustomization whose body comes from the `modes/{aws,on-prem}/` set a
site reconciles: on-prem it IS the real MinIO tenant (`wait:true`, so consumers wait for
buckets + IAM); aws it is an inert immediately-Ready marker (the lake is S3+IRSA). One
name, one object per mode → the `dependsOn` resolves in both with no collision and aws
stands up zero MinIO. (An earlier draft kept MinIO in aws to back the OPA bundle and
called the tenant "mode-independent"; moving opa to S3 (below) removed the last aws
MinIO consumer, so the tenant is on-prem-only.)

**opa moves to S3+IRSA (we own the chart).** `scout-opa` is a Scout chart, so rather than
leave the authz bundle on MinIO in aws we added an IRSA `serviceAccount` template + a
conditional bundle-reader `envFrom` to the chart (mirroring the sibling Scout charts that
already ship one). In aws opa's bundle plugin reads from S3 via the IRSA SA (no MinIO, no
static Secret); on-prem it keeps the MinIO reader Secret. The Keycloak OPA-bundle *writer*
SPI does the same (its blank-keys-→-IRSA path already existed, no Java change). So the
whole OPA authz-bundle pipe is S3+IRSA in aws, and MinIO is genuinely on-prem-only. (An
earlier draft treated scout-opa's missing SA template as a fixed constraint and left opa on
MinIO. We own the chart, so we changed it.)

**The secret contract is mode-specific** and documented in `required-secrets.md`: cloud
uses IRSA (no object-store access-key Secrets), on-prem uses the MinIO-user credential
Secrets. `service_mode` and the edge together determine which secrets a site provisions.

The shared DAG (postgres, keycloak, cassandra, elasticsearch, temporal, valkey, the
analytics apps) stays mode-agnostic; only the edge moves.

## Alternatives considered

- **Chart-internal branching on `service_mode`** (pass the mode as a value; the chart
  conditions the catalog/envFrom/volumes/SA). We DO own most of these charts (`scout-opa`,
  `hive-metastore`, the extractor charts), so this is on the table, and we use it when the
  mode needs a chart *capability* rather than data: `scout-opa` gained a `serviceAccount`
  template for its aws IRSA SA. But when the delta is a per-mode *values* block (envFrom
  membership, the Trino catalog's present/absent lines, hostPath-vs-emptyDir), a
  `valuesFrom` edge ConfigMap keeps it as reviewable data in the deploy base instead of
  scattering `if aws` branches through chart templates, and it is the *only* option for
  the genuinely upstream charts we can't modify (trino, superset). Rule of thumb: add a
  template capability to a chart we own when the mode needs one; carry per-mode values in
  the edge ConfigMap either way. (This corrects an earlier draft that avoided chart changes
  by wrongly treating our own charts as unmodifiable.)
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
  ingress edge set, once that lands). The first `aws`-mode consumer is a cloud cluster, set via a
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
- MinIO is **on-prem-only** (the `storage-ready` gate); opa and the Keycloak OPA-bundle
  writer both moved to S3+IRSA in aws (`scout-opa` gained an IRSA `serviceAccount`
  template, since we own it), so an aws cluster stands up **zero MinIO**.
- **Identity is per-component IRSA**, matching the live cluster convention: one
  `irsa_role_prefix` cluster-var, and each aws-edge ServiceAccount appends its component
  suffix (`-hive-metastore`, `-trino`, `-hl7log-extractor`, `-opa-bundle-reader`, ...), a
  least-privilege role per workload provisioned in the platform repo. New vars:
  `irsa_role_prefix`, `opa_bundle_s3_endpoint`.
- The **ingress edge is implemented** as `base/edge-{on-prem,aws}/`: on-prem Traefik
  forwardAuth Middlewares vs aws ALB-native-OIDC Ingresses (Keycloak un-gated + an admin
  fixed-response; Superset ALB-OIDC). Adds `acm_cert_arn` + `alb_group_name` to the
  contract and `alb-oidc-keycloak` (the oauth2-proxy client's id + secret, which the ALB reads for OIDC) to
  the site-seeded secrets; scheme comes from the `alb`/`alb-internal` IngressClassParams
  (Layer-0, which override the per-ingress annotation on EKS Auto). Only Superset +
  Keycloak are covered so far (the base's public components); jupyter, launchpad, and
  monitoring follow as those components land in the base.

## Related

- ADR 0011 (service-mode layered architecture), extended here to the deploy base.
- ADR 0031 (GitOps deployment base), the base + artifact this parameterizes.
- ADR 0030 (two-lane versioning), the artifact publish lane that ships both edges.
- ADR 0012 (security scan response and hardening), whose edge security-header middleware moves app-side in aws.
- ADR 0009 (Open WebUI CSP), the app-side header model aws mode relies on.
- `deploy/required-secrets.md`, `deploy/required-vars.txt`, the per-mode contracts.
