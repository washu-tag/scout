# ADR 0021: OPA User Attribute Distribution via MinIO Bundles

**Date**: 2026-05-22
**Status**: Accepted
**Decision Owner**: TAG Team

## Context

ADR 0020 establishes Trino RBAC via OPA with per-user attributes (`allowed_facilities`, `allowed_modalities`, `mask_phi_fields`) stored on the user object in Keycloak. OPA needs those attributes available at policy-evaluation time to compute row filters and column masks, and the system needs to satisfy a stated security requirement: a user disabled in Keycloak loses Trino access within seconds, not minutes.

The design question is **how OPA gets user attribute data**. Three families of solutions exist:

- **Pull on decision** — OPA calls Keycloak's admin API (e.g., via Rego's `http.send`) for each cache miss. Simple in the policy; couples decisions to Keycloak availability; cache invalidation becomes a separate problem.
- **Push to replicas** — a listener watches Keycloak admin events and PUTs per-user data to each OPA pod via its data API. Fast on writes; introduces multi-replica fan-out and pod-replacement edge cases.
- **Pull from canonical store** — a listener writes the attribute snapshot to a separate store (object storage, KV, etc.) and OPA pulls from there using its native distribution mechanism. Decisions are local-only; multi-replica convergence is free.

### Goals

1. **Sub-15-second offboarding** end-to-end (a user disabled in Keycloak loses Trino access within seconds, satisfying ADR 0020's stated real-time requirement).
2. **All OPA replicas converge to the same state** without cross-replica coordination code.
3. **Pod restarts recover without manual intervention** — new OPA replicas come up with full data before serving traffic.
4. **Decisions don't depend on Keycloak availability**. A Keycloak outage should not degrade in-flight Trino authorization.
5. **Minimal new image surface for partner sites.** Scout's air-gapped deployments require any new container image to be mirrored to the partner's registry, scanned, and patched on their cadence (ADR 0017). New images have a meaningful operational cost across deployments.

## Decision

**A Keycloak SPI event listener (`OpaUserBundlePublisherProvider` in `keycloak/event-listener/`) maintains an in-memory snapshot of all Scout users' RBAC-relevant attributes and publishes the snapshot as an OPA bundle (gzipped tar) to a dedicated bucket in MinIO. OPA's built-in bundle plugin pulls from MinIO every 5–10 seconds and atomically swaps its `data.users` subtree. The Rego policy reads `data.users[user]` directly — no `http.send`, no admin-API client, no cache-busting timestamps.**

## Alternatives Considered

### OPA pulls attributes from Keycloak via `http.send` with a TTL cache

Rego policy issues an `http.send` to Keycloak's admin API for each unique `(user, attribute-set)` lookup, with `force_cache_duration_seconds` (e.g. 60s) as the staleness bound. Listener does nothing (or pushes per-user invalidation timestamps the policy threads into the URL to bust the cache key).

**Rejected**: keeps decisions coupled to Keycloak — every cache miss is a Keycloak admin-API round-trip, the policy holds a client_credentials token, and a Keycloak outage degrades Trino authorization. Multi-replica fan-out is unsolved: invalidation pushed to one OPA pod doesn't reach the others until their independent TTLs elapse. Cache-busting timestamps (URL-as-cache-key) are non-obvious for future Rego readers — the mechanism doesn't model the problem, it works around OPA's lack of a per-key cache eviction API. Misses goal 4 directly and creates ongoing conceptual debt.

### Push attributes to each OPA replica via headless service + per-pod fan-out + init-container backfill

Listener resolves a headless service to all pod IPs and PUTs the full attribute payload to each replica. New OPA pods run an init container that walks Keycloak to seed local data before the main container becomes Ready.

**Rejected**: solves the right problem (data is canonical, no Keycloak dependence at decision time), but adds three pieces of bespoke distribution logic — DNS multi-resolve, init-container backfill, and a backstop reconcile loop for missed PUTs — that OPA's bundle plugin solves natively. Init-container readiness gating in particular re-implements behavior the bundle plugin provides for free. The implementation is uniformly worse than letting OPA pull.

### OPAL (Open Policy Administration Layer)

Open-source control plane from Permit.io: an OPAL Server centrally watches data sources and a git policy repo; OPAL Clients alongside each OPA receive updates via WebSocket and PUT to local OPA. Has a maintained Helm chart, ships GitOps-for-policy out of the box, and converges sub-second on data changes. Production-tested by Permit.io's commercial SaaS.

**Rejected for now**: best long-term fit if Scout grows to multiple OPA-enforced services, but the additional surface today is real — two new Python/FastAPI container images to vendor at partner sites (CVE stream, registry mirror, image scanning), a control-plane component to monitor and upgrade, plus the WebSocket reconnection edge cases. Scout currently has one OPA-enforced service (Trino RBAC). The marginal benefit over MinIO+bundles for one consumer is "policy GitOps" (which we can add cheaply with a small CI bundle pipeline) and "sub-second instead of 5–10 s data propagation" (which exceeds the stated requirement). Migration to OPAL later is mechanical: the Rego and the listener's responsibilities don't change; the storage target swaps. We defer the OPAL decision until the multi-consumer case materializes.

### Valkey as intermediate handoff between Keycloak and OPA

Listener writes per-user attributes into Valkey; OPA reads from Valkey on demand or via a bridge process. Scout already runs Valkey (ADR 0013).

**Rejected**: OPA has no native Valkey/Redis reader. Every integration path either (a) requires a custom OPA binary with a Go plugin — significant lift, ongoing upstream-tracking, defeats the "use the supported pattern" benefit, (b) re-introduces per-decision network calls via `http.send` to a Valkey-fronting HTTP service — the property we're explicitly trying to eliminate, or (c) builds a sidecar that polls Valkey and PUTs to OPA — at which point Valkey is fancy intermediate storage between a listener (which already has an in-memory map) and a bundle-shaped distribution layer that does the actual work. Valkey solves problems Scout doesn't have: shared state across multiple Keycloak listener replicas (Scout's Keycloak is `instances: 1`) and listener-side restart persistence (the listener recovers by re-walking Keycloak — same pattern the existing `user-approval-email` listener uses for its own bootstrap).

### Single OPA replica

Reduce OPA to one replica. The replication problem disappears.

**Rejected**: makes OPA a single point of failure for every Trino decision. A 30-second outage during rolling update or eviction is a 30-second Trino-analytics outage. Not aligned with the production-grade direction documented in ADR 0020.

### Accept the staleness floor; ship no invalidation

Drop the listener entirely. Live with a multi-minute TTL as the offboarding bound.

**Rejected**: ADR 0020 lists real-time offboarding as a stated security requirement — "a user disabled at their partner institution must lose access within seconds, not at the next sync interval." Minutes-scale staleness isn't seconds.

## Consequences

### Positive

- **No new infrastructure to operate.** MinIO is already a critical Scout dependency. The marginal operational footprint is one bucket and two service accounts. No new pods, no new images for partner sites to mirror through their air-gapped registries.
- **The Rego policy is simple.** `data.users[user]` is the entire read path — no `http.send`, no admin-token-fetch rule, no JWT-`exp` self-validation, no cache-busting URL construction.
- **OPA's bundle plugin handles distribution natively.** Polling, ETag-conditional GETs, atomic data-tree swap, last-known-good on-disk persistence, readiness gating on first successful load — all built in, all battle-tested in production OPA deployments at scale. Scout writes none of this code.
- **All replicas converge without cross-replica coordination.** Each OPA pod polls independently on its own clock; they reach the same `data.users` snapshot within one pull interval. No fan-out from the listener, no gossip protocol, no leader election.
- **Cold start works correctly.** OPA's bundle plugin doesn't let the pod declare Ready until the first bundle load succeeds, so new replicas join the Service endpoints with full data already loaded. No init-container race window.
- **Decisions survive Keycloak outages.** OPA serves the last-pulled bundle indefinitely from its on-disk cache. Stale-but-correct authorization continues during Keycloak maintenance windows.
- **Air-gapped friendly.** Bundle storage is in-cluster MinIO. Nothing new for partners to mirror, scan, or patch.

### Negative

- **~200 LOC of bespoke Java in the Keycloak SPI listener** for the in-memory user map, debounce timer, tarball assembly, and S3 PUT. The subtle failure mode is concurrent-modification-during-serialization; mitigated with copy-on-write snapshot semantics. Reviewed once and maintained on Scout's normal Java cadence — the same `keycloak/event-listener/` artifact that already ships `user-approval-email`.
- **Listener becomes (lightly) stateful.** In-memory user-attribute map, lost on listener pod restart. Recovered by walking Keycloak at `postInit` — same pattern the existing user-approval-email listener uses for its own bootstrap. No persistent storage required.
- **5–10 second steady-state staleness on data updates.** Within "seconds" as ADR 0020 phrases the requirement; tunable downward by reducing the bundle polling interval at proportional MinIO request-rate cost.
- **Policy distribution remains manual.** Rego in `policy/` is rendered into the OPA ConfigMap by the Ansible role and applied on `make install-opa`. No automatic deploy on commit to main. Mitigated cheaply when wanted: OPA's bundle plugin supports a separate policy bundle, and a CI job that publishes one is ~30 LOC of GitHub Actions. Deferred until the manual deploy step starts to chafe.

## Implementation Notes

### Bundle structure

```
bundle.tar.gz
├── .manifest          # { "revision": "<epoch_ms>", "roots": ["users"] }
└── users/
    └── data.json      # { "alice": {...}, "bob": {...}, ... }
```

`roots: ["users"]` declares ownership of the `data.users` subtree. OPA's bundle plugin uses `roots` to scope atomic swaps, so the user bundle doesn't collide with the static data (`filtered_tables`, `hidden_tables`, `attribute_filters`, `masked_columns`) rendered into OPA's ConfigMap by the Ansible role.

### Per-user payload shape

```json
{
  "allowed_facilities": ["WUSM", "BJH"],
  "allowed_modalities": ["CT", "MR"],
  "mask_phi_fields": ["false"],
  "enabled": true
}
```

Attribute values mirror Keycloak's `Map<String, List<String>>` user-attribute shape (multivalued by default), so the listener writes them verbatim — no per-key schema in two places. `enabled` is the one exception: it's a bare bool synthesized from the user's Keycloak enabled flag, not a user-profile attribute. `enabled: false` (and a missing user entry) both resolve to the deny-all default in Rego; the explicit `enabled: false` is preferred because operators can observe it directly in OPA's data document when diagnosing "why can't alice query."

### MinIO topology

- Bucket: `opa-bundles`. Versioning enabled so a bad bundle can be rolled back by selecting a prior object version.
- Two service accounts via the existing MinIO Ansible pattern (mirrors `s3_lake_reader` / `s3_lake_writer`):
  - `opa-bundle-writer`: `PutObject` on `opa-bundles/scout/bundle.tar.gz` only. Mounted into the Keycloak pod via a Kubernetes Secret + envFrom.
  - `opa-bundle-reader`: `GetObject` on `opa-bundles/scout/*`. Mounted into each OPA pod the same way.

### Keycloak listener

`OpaUserBundlePublisherProvider` lives in `keycloak/event-listener/`, alongside the existing `user-approval-email` listener (same JAR, same operator `additionalOptions` plumbing).

- `postInit()` walks `session.users()` to build the initial in-memory `Map<String, UserAttrs>` and writes the first bundle to MinIO.
- `onEvent(AdminEvent)` handles `USER` `UPDATE` / `CREATE` (refresh that user's entry from `session.users()`) and `DELETE` (remove from map). For DELETE, the username comes from `event.getDetails().get("username")` since the user row is already gone by the time the event fires.
- Each event marks the bundle dirty and schedules a debounced write (1-second window). The debounce coalesces a burst of admin actions into one S3 PUT.
- On debounce fire: take a copy of the map under a lock, render JSON, build the tarball in-memory, PUT to MinIO with `Content-Type: application/gzip`.

Env vars threaded through the Keycloak operator's `additionalOptions`: MinIO endpoint URL, bucket name, object key, region, realm, access key, secret key (see `ansible/roles/keycloak/tasks/deploy.yaml`).

### OPA configuration

The OPA Helm values include `services` and `bundles` blocks:

```yaml
services:
  minio:
    url: http://minio.scout-data.svc:9000
    credentials:
      s3_signing:
        environment_credentials: {}   # reader creds from mounted Secret env vars

bundles:
  scout-users:
    service: minio
    resource: opa-bundles/scout/bundle.tar.gz
    polling:
      min_delay_seconds: 5
      max_delay_seconds: 10
    persist: true
```

`persist: true` writes the last successfully loaded bundle to a Pod-local emptyDir so a restart during a MinIO outage doesn't drop OPA to deny-all.

### Rego shape

`policy/trino/main.rego` reads user attributes directly from `data.users`:

```rego
default user_attrs := {"enabled": false}
user_attrs := data.users[input.context.identity.user] if identity_present
```

An `enabled` check gates the rules that consume `user_attrs`, so a disabled user gets deny-all even if their attributes are still in the bundle (covers the "DELETE event in flight" race). Service principals (`superset_svc`, `openwebui_mcp_svc`, `voila_svc`) aren't in `data.users` — the `is_system_identity` carve-out exempts them from the `user_enabled` gate.

### Failure modes

- **MinIO down**: listener PUT retries with backoff and a bounded in-memory queue of the latest pending writes. OPA replicas serve their last-pulled bundle from persistent storage.
- **Listener pod dies**: bundle in MinIO survives. New listener pod's `postInit` walks Keycloak and writes a fresh bundle; events fired during the downtime are picked up by the walk because Keycloak is the source of truth.
- **OPA pod dies**: replacement pulls the bundle from MinIO during startup; readiness gating ensures it joins endpoints only after successful load.
- **All control-plane services down**: OPA replicas serve their on-disk persisted bundle until storage returns. Trino keeps authorizing with the last-known-good attribute snapshot.

## Future Considerations

- **GitOps for policy**. Add a CI job that builds an OPA policy bundle on merge to `main` and writes to MinIO alongside the data bundle. OPA's bundle plugin supports separate policy and data bundles natively. Roughly 30 LOC of GitHub Actions plus a small Ansible change to add the policy bundle to OPA's config. Defer until "deploy via Ansible to push a rego change" becomes friction.
- **Migration to OPAL**. If Scout grows past 3+ OPA-enforced services (e.g., OPA gating Open WebUI's per-tenant model access, OPA on Voila's ingress for the same impersonation pattern as MCP per ADR 0022), the operational case for OPAL strengthens. The Rego is transport-agnostic; the listener's "compute attribute payload" code is reusable; the cutover swaps "PUT bundle to MinIO" for "POST inline update to OPAL Server." Mechanical when needed.
- **Per-tenant bundles**. If Scout grows to per-tenant policy customization (different masking rules, different `attribute_filters` per partner), the bundle pattern supports it via multiple bundles with disjoint `roots`. Out of scope today.
- **Bundle signing**. OPA supports bundle signature verification (`signing.keyid`, `signing.scope`). MinIO bucket policy is the integrity boundary today; if supply-chain integrity becomes a stated requirement (e.g., for a partner with stricter compliance posture), bundle signing is a small additive change on both ends.

## References

- ADR 0005: MinIO STS Authentication Decision — informs the `opa-bundle-writer` / `opa-bundle-reader` service-account pattern.
- ADR 0011: Deployment Portability via Layered Architecture — the service-mode pattern this ADR layers under.
- ADR 0013: Redis Enterprise to Valkey Migration — explains Scout's existing Valkey footprint; relevant to the Valkey-as-handoff rejection.
- ADR 0017: Air-Gapped Package Proxy — explains why minimizing new container images for partner sites carries operational cost.
- ADR 0020: Trino Authorization via OPA with Keycloak Attributes — the architecture this ADR's distribution mechanism plugs into.
