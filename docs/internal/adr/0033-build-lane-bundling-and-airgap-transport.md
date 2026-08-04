# ADR 0033: Build-Lane Bundling and Air-Gap Transport via Hauler

**Date:** 2026-08
**Status:** Proposed
**Decision Owner:** TAG Team

## Context

ADR 0030 §1 calls for a build lane where every merge publishes its changed
components and records everything else, at its existing digest, in "a small
signed document pushed to the registry", the authoritative answer to "what is
Scout at `0.YYYYMMDD.<run>`". ADR 0031 §7 then relies on a staging-node
reconciler to carry that content across the soft air gap into Harbor, where the
cluster's containerd mirror resolves image digests and Flux fetches charts and
the config artifact by digest with cosign `.spec.verify`.

Phase 2 began implementing the "small signed document" as bespoke code: a JSON
build manifest with a hand-written schema and validator (`tooling/manifest/`), a
change classifier that re-derives rebuild-vs-carry (`classify.py`), a custom
digest-capture step in the docker-push action, and a planned publish job that
would `oras`-push the manifest and hand-roll the air-gap mirror.

That is a wheel that already exists. The record we need is a versioned,
digest-pinned, signed set of images and charts that relocates across an air gap
into a private registry with digests intact, and OSS tooling is built for
exactly that. We evaluated the field (Carvel imgpkg/kbld, Zarf, Timoni,
Helmfile, oc-mirror, plain oras) against seven criteria: pin every image and
chart by digest, carry unchanged forward, one signed artifact, air-gap
relocation, fit under the existing Flux consumer, handle images and charts, and
retire custom code.

## Decision

Adopt **Hauler** (hauler-dev / Rancher Government) with **keyed cosign** as the
build-lane bundler, signer, and air-gap transport. Flux stays the consumer,
unchanged.

- **The record is a Hauler haul, not hand-rolled JSON.** CI resolves the digest
  set for the build (changed components rebuilt; unchanged resolved live at
  their current stable-tag digest), renders a Hauler content manifest
  under `kind: Images` for both the images and the OCI charts, each pinned by
  digest (OCI charts ride as `kind: Images` so they copy verbatim; `kind: Charts`
  re-packages them under a `hauler/<name>` path with a new digest, which would
  break the chart digest and path Flux pins, POC-confirmed), then
  `hauler store sync -f <manifest>
  --key cosign.pub` pulls and verifies every artifact into a local OCI store and
  `hauler store save` collapses it into one `scout-0.YYYYMMDD.<run>.tar.zst`.
  That haul is the versioned, relocatable "what is Scout at this version" record
  ADR 0030 §1 asks for; it carries each artifact with its own keyed cosign
  signature (not one signature over the archive, see the open sub-decision).
- **Air-gap transport is `hauler store copy`, on the ADR 0031 staging
  reconciler.** The reconciler runs `hauler store load` on the sneakernetted
  tarball then `hauler store copy registry://harbor.<site>` to push every image
  and chart into Harbor **preserving the original sha256 digests byte for byte**.
  The cluster is untouched: containerd resolves `ghcr.io/...@digest` image refs
  through the Harbor mirror, Flux `OCIRepository`/`chartRef` fetch charts and the
  config artifact by the same digests, and cosign `.spec.verify` (keyed, via a
  public-key `secretRef`) checks the per-artifact signatures fully offline, with
  no Rekor/Fulcio reachability. (`--use-tlog-verify` is Hauler's own sync-time
  flag, defaulting off; it is unrelated to Flux's in-cluster verification.)
- **Redeploy-only-on-change still comes from Flux + stable digests**, exactly as
  ADR 0030/0031 intend: an unchanged component keeps its prior digest, so nothing
  restarts it. Hauler is pure digest-preserving transport, not a reconciler; it
  does not compete with Flux, inject a registry, or run an in-cluster agent.
- **Keyed cosign, not keyless.** Air-gapped verification cannot reach a Sigstore
  transparency log, so signing uses a managed key (matches Phase 2 open decision
  2). The key lifecycle (generation, escrow, rotation) is the ADR 0031 Layer-0
  item this makes concrete.
- **Preconditions for the verify chain.** Two enablements must be in place, both
  one-time: (a) images and charts are cosign-signed with the managed key **at
  publish to ghcr**, so `store sync --key` can verify them and their signatures
  ride along in the haul (unsigned upstream means nothing to verify or carry);
  and (b) the enclave Harbor supports OCI 1.1 referrers and `hauler store copy`
  carries the signature objects with their subject digests, so Flux
  `.spec.verify` can find them after relocation. Confirm both before the air-gap
  phase.

### The one open sub-decision

A Hauler haul carries **per-artifact keyed cosign signatures plus the tarball's
own integrity**, not a single signature over one merkle-rooted index of all
components. Flux already verifies each image and chart independently via
`.spec.verify`, so per-artifact signing is the natural and sufficient trust
model. If the team instead wants **one signature over the whole platform** as a
single audit object, that is the one place Carvel imgpkg (an `ImagesLock`
Bundle) or Zarf (a whole-package signature) is stronger, at the Flux-fit cost in
Alternatives below. Recommendation: accept per-artifact signing; do not take on
imgpkg's or Zarf's consumer-side baggage for a single-signature nicety Flux does
not need.

## Consequences

- **Retires custom code.** The publish job's `oras`-push, signing, and air-gap
  mirror scripting collapse into `hauler store sync/save/load/copy` + cosign. The
  serialize/validate half of `tooling/manifest/` and its bespoke `schema.json`
  are replaced by the Hauler content manifest (a maintained tool's input format).
  The `classify.py` scope enum is redundant with the existing `dorny/paths-filter`
  `changes` job and the build `if:` guard and comes out. See the follow-up plan.
- **What stays custom is small and load-bearing.** Deciding the rebuild set and
  resolving the digest set (changed components at their fresh digest, unchanged
  at their current stable-tag digest) is a build-lane decision no bundler owns; it
  reduces to a thin step that renders the Hauler manifest. The `appVersion`
  <-> image coupling (ADR 0030 §2) is likewise producer-side authoring.
- **One artifact, not two.** The Hauler haul is the ADR 0030 §1 record; do not
  also maintain a separate JSON manifest, or the two can disagree. The ADR 0031
  config artifact continues to stamp `name:tag@digest` into the deploy base from
  the same resolved digest set.
- **New dependency, small.** Hauler is a single static Go binary added to CI and
  to the staging-reconciler host; no cluster-side component. Carvel-style
  maintenance risk does not apply (no in-cluster controller).
- **Enables an explicit air-gap runbook.** `sync` + `save` on the connected side,
  sneakernet, `load` + `copy` into Harbor in the enclave, `cosign verify --key
  cosign.pub --insecure-ignore-tlog` for offline attestation.

## Validation

Throwaway POCs against real published artifacts confirmed the mechanics the
deploy model depends on:

- **Digest-preserving transport (images).** A `kind: Images` manifest for
  `hl7-listener` + `hl7log-extractor` -> `hauler store sync` (497.6 MB) ->
  `store save` (one 315 MB `.tar.zst`) -> `store load` into a fresh store ->
  `store copy` into a private registry. The `hl7-listener` digest
  (`sha256:2b7b4c3a...3a4a`) was **byte-for-byte identical** on ghcr and in the
  destination after the round trip, and the repo path was preserved (only the
  host changes), confirmed by Hauler's push log and an independent registry v2
  `Docker-Content-Digest` check.
- **OCI charts.** Bundled as `kind: Images`, the real `hl7-transformer` OCI
  chart copied verbatim: digest (`sha256:63a61a3b...1314f6`) and path
  (`.../charts/hl7-transformer`) preserved, and it stayed a valid Helm artifact
  in the destination (config media type `application/vnd.cncf.helm.config.v1+json`).
  `kind: Charts` instead re-packages a chart under `hauler/<name>` with a new
  digest, which is why the design lists charts under `kind: Images`.
- **Signature relocation.** `hauler store sync` carries cosign material by
  default, and `store copy` pushed the cosign **OCI 1.1 referrers** by digest
  into the destination (visible in the copy log as `@sha256:...` pushes; they are
  referrers, not legacy `.sig` tags, so a plain tag list does not show them).
  This is precondition (b), and it holds wherever the destination registry
  supports OCI 1.1 referrers.
- **Keyed offline verification.** `cosign sign --key ... --use-signing-config=false
  --tlog-upload=false` then `cosign verify --key --insecure-ignore-tlog` succeeded
  against the relocated chart and image with no Sigstore reachability; a wrong key
  was rejected. On cosign v3.1.2 `--use-signing-config` defaults true and conflicts
  with the tlog opt-out, so both flags are needed to sign offline (`--tlog-upload`
  is deprecated in favor of a `--signing-config` carrying no transparency-log
  services).

`store copy` lands every artifact by digest only (tags do not relocate), so
enclave references must be digest-pinned, which the build manifest already
guarantees. Because digests are identical on both sides of the gap, the
`ghcr.io/...@digest` references Flux and containerd resolve against Harbor point
at the same content, so pods restart only when a component actually changed,
exactly as ADR 0030/0031 intend.

## Relationship to ADRs 0030 and 0031

This refines, it does not overturn. ADR 0030 §1's build manifest is realized as a
Hauler haul; ADR 0031 §7's staging-reconciler mirror step is `hauler store copy`.
Both ADRs' consumer contract (Flux, digests, containerd mirror, cosign
`.spec.verify`) is unchanged. If ADRs 0030/0031 are still in draft when this is
accepted, fold the mechanism references into them and keep this as the tool
decision and its evaluation of record.

## Alternatives Considered

- **Custom build manifest (the Phase 2 starting point).** A hand-rolled JSON
  record with its own schema, validator, digest capture, `oras` push, and mirror
  scripting. Rejected: it reinvents packaging, signing, and relocation that Hauler
  provides as one maintained binary, and the air-gap mirror is exactly the
  fragile custom surface we want gone.
- **Zarf (runner-up).** The most complete air-gap package manager, with one
  whole-package signature and free SBOMs. In "mirror-only" mode
  (`zarf package create` + `mirror-resources` into Harbor) it fits under Flux and
  does what Hauler does, but heavier. Its native mode injects an in-cluster
  registry, a git server, and a cluster-wide `zarf-agent` mutating webhook that
  rewrites Flux sources to `zarf-registry:<tag+crc>` (not `@sha256`, not Harbor),
  overlapping the committed Harbor + pure-Flux design. Worth keeping as the
  fallback if a single whole-package signature becomes a hard requirement.
- **Carvel imgpkg + kbld.** Cleanest single-bundle semantics (`ImagesLock` is a
  digest-pinned lock), but its relocation-aware consumer is kapp-controller, which
  ADR 0031 rejected in favor of Flux; Flux ignores `ImagesLock` relocation, so
  you would bolt on a `kbld` render step or fall back to Harbor pull-through, at
  which point Hauler's verbatim copy is simpler. Charts are second-class.
- **Timoni / Helmfile / oc-mirror / plain oras+regctl.** Poor fit: Timoni and
  kapp-controller replace Flux and rewrite charts into their own module format;
  Helmfile cannot pin OCI charts by digest and has no image or bundle story;
  oc-mirror is OpenShift/OLM-centric; plain oras/regctl is a toolkit that
  reproduces the custom manifest by hand and retires nothing.
