# ADR 0036: Managed Data Services in aws Mode (RDS + OpenSearch)

**Date**: 2026-09
**Status**: Proposed.
**Decision Owner**: TAG Team

## Context

ADR 0035 established a `service_mode` (`aws` | `on-prem`) that splits the storage/identity and
ingress/auth edges by platform. It did **not** extend to the **data tier**: the deploy base runs
PostgreSQL as a self-hosted CloudNativePG (CNPG) `Cluster` and Elasticsearch as a self-hosted ECK
`Elasticsearch` in **both** modes.

Self-hosting stateful databases in-cluster is the right default on-prem (air-gapped, no managed
services), but it is an anti-pattern in a cloud (aws) deployment:

- **Operational burden.** Backups, PITR, failover, minor-version patching, and storage scaling are the
  operator's job when self-hosted, and the platform's job on RDS / OpenSearch. A cloud site pays for
  compute + EBS to run a database its platform already offers as a service.
- **Blast radius.** The database lives in the same cluster it serves, so a namespace deletion, a node
  recycle (EKS Auto consolidation), or a botched migration can take the data with it. Observed: a cutover
  that reverted a self-hosted CNPG + ECK saw the namespace-deletion cascade destroy the backing EBS
  volumes (Delete-reclaim PVs); recovery depended on out-of-band snapshots. A managed endpoint is
  decoupled from the cluster's lifecycle.
- **Cutover fragility.** Adopting an existing operator-managed CR in place hits server-side-apply
  conflicts on fields the operator defaults: CNPG stores `postgresql.parameters` values as typed scalars,
  and cass-operator populates `podTemplateSpec.spec.containers`, so re-applying a differently-authored CR
  over the live object fails the dry-run. A managed endpoint has no in-cluster CR to adopt, so this class
  of conflict disappears.

## Decision

Extend `service_mode` to the data tier. Postgres and Elasticsearch become **mode-selected between a
self-hosted operator (on-prem) and an external managed endpoint (aws)**, consumed through a connection
abstraction rather than a hardcoded in-cluster service name.

**postgres**
- **on-prem**: CNPG `Cluster` as today (the `postgres-cluster` Flux Kustomization).
- **aws**: no CNPG `Cluster`. Consumers connect to an external **RDS** instance via `${postgres_host}`
  (+ `${postgres_port}`) and a site-provided credential Secret. `postgres-cluster` becomes on-prem-only,
  the same way ADR 0035 makes `storage-ready`/MinIO and the Traefik edge on-prem-only.

**elasticsearch**
- **on-prem**: ECK `Elasticsearch` as today (the `elasticsearch-cluster` Kustomization).
- **aws**: no ECK cluster. The extractor + consumers connect to an external **OpenSearch** domain via
  `${es_host}` and a site credential Secret. `elasticsearch-cluster` becomes on-prem-only.

**The seam** is a per-service host cluster-var (`${postgres_host}`, `${es_host}`) that resolves to the
in-cluster service name on-prem (e.g. `postgresql-cluster-rw.<ns>.svc`) and to the managed endpoint on
aws. Every consumer (hive-metastore, keycloak, superset, extractor, launchpad) already reads its DB/ES
host from config; they switch to the cluster-var. Credentials stay fixed-name Secrets (ADR 0031); an aws
site materializes them from RDS/OpenSearch instead of from the operator-generated Secret.

**cassandra (temporal)** stays self-hosted (cass-operator) in **both** modes for now. Temporal's
cassandra-schema tooling plus the operator's rack/repair/topology management make Amazon Keyspaces a
non-trivial swap (protocol, LWT, tunable-consistency differences). Managed cassandra for temporal is an
explicit follow-up, out of scope here. Its adopt-in-place conflict (above) is handled separately: either
force-apply the `CassandraDatacenter`, or exclude it from the adopt and let cass-operator keep ownership.

## Consequences

- **aws sites provision RDS + OpenSearch out-of-band** (site IaC) and pass the endpoints as cluster-vars
  + the credentials as the fixed-name Secrets. Same principle as ADR 0035: the artifact expresses both
  modes; the site selects one and supplies that mode's inputs.
- **Removes the CNPG/ECK adopt-in-place conflict** for postgres + ES in aws (no in-cluster CR to adopt),
  and removes the in-cluster blast radius for those tiers.
- **on-prem is unchanged.**
- **Migration** from an existing self-hosted deployment to managed is a one-time dump/restore (`pg_dump` /
  reindex or snapshot-restore) at the aws cutover; flipping the connection cluster-var is the switch.
- **Contract additions**: `postgres_host`/`postgres_port`/`es_host` (+ any TLS opts) in `required-vars.txt`;
  the RDS/OpenSearch credential Secrets in `required-secrets.md`.
- **Cost/latency**: managed endpoints add a network hop vs an in-cluster pod; acceptable for the
  operational + durability gains.

## Alternatives considered

- **Keep self-hosting in aws** (status quo): rejected — operational burden, blast radius, and the cutover
  fragility above.
- **Self-host but harden** (Retain reclaim, force-apply, PVC guards): mitigates the data-loss + conflict
  symptoms but keeps the operational burden, and is strictly worse than a managed endpoint in a cloud that
  offers one. (Still the right hardening for on-prem and for the cassandra tier that stays self-hosted.)
