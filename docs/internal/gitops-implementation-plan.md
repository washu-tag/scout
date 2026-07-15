# GitOps & Versioning — Implementation Plan

Phasing for ADRs 0030/0031 (orientation:
`docs/internal/adr/0030-0031-tldr.md`). Phases are ordered by dependency,
not dated; each has a definition of done. Items become tickets when their
phase starts.

## Phase 0 — chart publishing and chart-owned config

*No dependencies; highest value per effort.*

- A chart publish job: charts whose directory changed publish to
  `oci://ghcr.io/washu-tag/charts/<name>` at the day's build tag. A plain
  per-chart path filter — no manifest yet. Only changed charts: chart
  versions land in pod-template labels, so packaging everything on every
  merge would restart pods for no reason. Consumers pin exact chart
  versions; Renovate bumps them.
- Chart-owned derived config, spark-defaults first: the hl7-transformer
  chart owns `spark-defaults.conf`, switched by service-mode values
  (ADR 0011 style).
- GitOps clusters switch chart sources from git-branch to the published
  OCI charts and delete the hand-copied spark-defaults ConfigMap. This
  switch is the moment the staleness problem actually closes.

Coupling images to charts (`appVersion`) lands in phase 2, not phase 0 —
and the phases ship as one release, so no consumer runs phase 0 without it.

**Done when:** no hand-copied derived-config manifests remain in any
cluster repo, and the next spark-defaults-class change reaches every
cluster as a chart version bump.

## Phase 1 — release automation

*Independent of phase 0; can run in parallel.*

- Repo settings: squash-only merges (disable rebase merges, currently also
  enabled), linear-history branch protection, up-to-date requirement on
  the release PR.
- PR-title lint (Conventional Commits) and the upgrade-notes check for `!`
  PRs.
- release-please accumulating release PR: computes version and changelog;
  merging it drives the existing release workflow. In-tree version
  stamping (`update-versions.sh`) stays until cutover — Ansible still
  reads stamped tags from release checkouts.

**Done when:** a release is cut by merging a release PR, with a generated
changelog and no hand-chosen version number.

## Phase 2 — the build pipeline (per-merge identity)

*Depends on phase 0 (extends the chart job). Phase 1's squash-only setting
should land first — the pipeline's ordering guard assumes linear history.*

- Per-merge image builds at `0.YYYYMMDD.<run>` for changed images. Change
  classification from paths; the path→image mapping lives next to the CI
  filter that uses it, in one reviewed place.
- The build manifest: pushed last, after every artifact it lists; change
  detection relative to the last successful manifest's source commit;
  concurrency group plus a guard refusing commits older than the last
  manifest; first run bootstraps from the current `latest` digests.
- Docs-only merges publish nothing; CI/build-tooling changes rebuild
  everything.
- Weekly scheduled full rebuild (re-baselines all digests, bounds
  classifier mistakes). `SOURCE_DATE_EPOCH` set in builds to cut needless
  digest changes on full rebuilds.
- Charts join the manifest: `appVersion` = the producing build of the
  chart's primary image; Scout-image charts default their image tag to
  `.Chart.AppVersion`.
- Release manifests publish at each release and are attached to the
  GitHub Release.
- Build version injected into images (build argument → UI footer, package
  metadata).

**Done when:** every merge that changes deployable source publishes a
manifest, and a dev cluster tracks the build lane through an ImagePolicy
with pods restarting only for components that changed.

## Phase 3 — the deploy/ base and config artifact

*Depends on phases 0 and 2 (charts to pin; a manifest to stamp from).*

- Kustomize base per component; Flux Kustomization dependency graph with
  `dependsOn` and CEL health checks; one-off operations as dependent Jobs;
  initial database creation into the CNPG bootstrap; feature flags as
  Kustomize Components.
- Keycloak realm decomposition into keycloak-config-cli fragments (base
  realm + per-component clients + user-profile attributes from the shared
  filter map) — the one item here that is design work, touching
  ADRs 0003/0020/0025.
- Config artifact publishing: placeholder references in git; chart pins
  and image references stamped from the build manifest at publish
  (`name:tag@digest`); `cluster-vars` + `StrictPostBuildSubstitutions` +
  the required-vars file; structured settings as site-overlay values via
  `valuesFrom`.
- CI switch: deploy-and-test deploys via Flux from the config artifact;
  the matrix expands to optional components (XNAT, playbooks, data
  generator, CPU-mode chat; GPU stays dev-cluster-proven); CI capacity
  (roughly +50%) provisioned; the Ansible deploy-and-test job retained,
  gated on `ansible/**`.

**Done when:** CI stands up the full platform, optional components
included, from the published config artifact, and passes the ingest and
authorization suites.

## Phase 4 — site repos and dev cutovers

*Depends on phase 3.*

- Site config repos for the dev/continuously-tracking clusters: pin +
  cluster-vars + components; Renovate against them (minimum version
  pinned for `OCIRepository` support); required-vars validation in
  site-repo CI.
- SOPS from day one in these repos (age keys; `.sops.yaml` recipients =
  cluster key + ops key; kustomize-controller decryption). Dev clusters
  prove the secrets path for a full phase before on-prem depends on it.
- Emergency-change runbook (`flux suspend` procedure, commit-before-resume
  contract, prolonged-suspension alert) — written before any cluster
  depends on it.
- Cut over the dev clusters wholesale: overlays on the base (including a
  different auth/ingress overlay where the edge differs), delete
  hand-written manifests, one adoption pass per cluster (reuse Helm
  release names, ownership relabeling).

**Done when:** every dev cluster converges from a base overlay, and one
upgrade has been performed end-to-end by merging a Renovate PR.

## Phase 5 — on-prem cutover

*Depends on phase 4; lands at a scheduled release.*

- Bootstrap Ansible: Flux install; generate the cluster age key and keep a
  recovery copy in the Ansible vault; one-time vault→SOPS secret migration
  (SOPS is the default; Ansible/vault materialization only where
  governance bans secrets in git); one-time site-repo seeding from
  `inventory.yaml` (write-once).
- Staging reconciler for air-gapped sites (validate required vars →
  package the site overlay → sign → push), with its CI harness (packaging,
  validation, signature verification) before it gatekeeps an upgrade.
- The cutover release: adoption pass; remove Ansible service roles and
  `make install-<service>` targets; retire `latest`, `VERSION` files,
  `derive-version`, and the demo-branch flow; releases move to
  publish-time stamping (the bump/reset commits die).
- The runbook ships with it: emergency changes, per-component proof
  status, backout-notes discipline.

**Done when:** an on-prem site has upgraded by merging a PR, and the
Ansible service roles are deleted from the repo.

## Deferred until needed

Mechanisms are specified in the ADRs, so these are triggers, not design
work:

- **Releases as promotions** (ADR 0030, Deferred work): re-label an
  existing tested build instead of rebuilding. Trigger: wanting releases
  to ship the exact bytes already validated, or release rebuilds becoming
  a cost/flakiness problem.
- **Hotfix release branches** (`release/X.Y` with `X.Y.Z-rc.<run>`
  builds). Trigger: the first backport a site needs. Until then, fixes
  ship as the next release, or via today's release workflow run from a
  branch.
- **Registry pruning** (keep digests referenced by retained manifests;
  never prune by tag age). Trigger: registry growth actually hurting.
  Until then: keep everything.
- **Hard-gap transport** (ADR 0031 Section 7, deferred block):
  completeness-gated copying, BOM enforcement, an archive path. Trigger: a
  committed fully disconnected deployment. Tooling evaluation done (prefer
  `oras copy`; flux-mirror and Hauler are candidates; Zarf rejected).

## Verify at implementation

- Harbor proxy-cache behavior for the Flux config-artifact media types
  (OCI Helm charts confirmed by Harbor maintainers, not in Harbor's docs;
  the Flux config-artifact media type gets a one-time check).
- Air-gapped chart/OCI-artifact host rewrite: source-controller ignores the
  containerd registry mirror, so charts and the deployment-config artifact
  carry the Harbor host via the `oci_registry` `postBuild` variable, ahead
  of the pinned digest. Confirm the substitution resolves and that
  `spec.verify` still passes — the cosign signature must be reachable
  through the same Harbor path as the artifact.
- Renovate minimum version for `OCIRepository` tag bumping.
- Flux ≥ 2.5 (CEL health checks); confirm `StrictPostBuildSubstitutions`
  behavior on the deployed version.
- Retention N (how many build manifests to keep) — chosen when pruning
  activates.

## Out of scope (separate workstreams)

- Environment promotion / progressive delivery / CD gates (future ADR).
- Managed-service swaps (Postgres/Valkey/Temporal persistence/inference) —
  the service-mode groundwork lands with chart-owned config; the swaps are
  their own plan.
- Hotfix support-window policy (support matrix decision).
