# ADR 0036: Managed Data Tier in aws Mode, and Temporal on PostgreSQL

**Date**: 2026-09
**Status**: Proposed.
**Decision Owner**: TAG Team

## Context

ADR 0035 established a `service_mode` (`aws` | `on-prem`) that splits the storage/identity and
ingress/auth edges by platform. It did **not** extend to the **data tier**: the deploy base runs three
self-hosted stateful systems in **both** modes:

- **PostgreSQL** as a CloudNativePG (CNPG) `Cluster` (hive metastore, keycloak, superset, extractor).
- **Cassandra** as a cass-operator `CassandraDatacenter` (Temporal's default persistence store).
- **Elasticsearch** as an ECK `Elasticsearch` (Temporal's visibility store).

Self-hosting stateful databases in-cluster is the right default on-prem (air-gapped, no managed
services), but it is an anti-pattern in a cloud (aws) deployment:

- **Operational burden.** Backups, PITR, failover, minor-version patching, and storage scaling are the
  operator's job when self-hosted, and the platform's job on a managed endpoint. A cloud site pays for
  compute + EBS to run databases its platform already offers as services.
- **Blast radius.** The database lives in the same cluster it serves, so a namespace deletion, a node
  recycle (EKS Auto consolidation), or a botched migration can take the data with it. Observed: a cutover
  that reverted a self-hosted CNPG + ECK saw the namespace-deletion cascade destroy the backing EBS
  volumes (Delete-reclaim PVs); recovery depended on out-of-band snapshots.
- **Cutover fragility.** Adopting an existing operator-managed CR in place hits server-side-apply
  conflicts on fields the operator defaults: CNPG stores `postgresql.parameters` values as typed scalars,
  and cass-operator populates `podTemplateSpec.spec.containers`, so re-applying a differently-authored CR
  over the live object fails the dry-run.

A naive "make aws fully managed" reading of this says: swap postgres to RDS, Elasticsearch to OpenSearch,
and cassandra to Amazon Keyspaces. That is three managed services, one of which (Keyspaces) is a hard swap
from Temporal's cassandra plugin (protocol, LWT, tunable-consistency differences).

**The observation that simplifies the whole tier:** cassandra and Elasticsearch each exist for exactly one
reason, and it is the same reason. Cassandra is Temporal's default store; Elasticsearch is Temporal's
visibility store. Nothing else in the deploy base consumes either one. They were inherited from the
temporalio Helm chart's default datastore combo, not chosen for a scale requirement Scout has (its
workload is batch HL7 extraction, well inside PostgreSQL's envelope). Temporal supports PostgreSQL for
**both** stores: the `default` store via the `postgres12` SQL plugin, and the `visibility` store via SQL
advanced visibility (GA since Temporal 1.20; Scout pins 1.31). So Temporal can run entirely on PostgreSQL,
and cassandra + Elasticsearch can be removed from Scout altogether.

## Decision

**1. The data tier is PostgreSQL-only, mode-selected between self-hosted (on-prem) and managed (aws).**

- **on-prem**: CNPG `Cluster` as today, behind the mode-agnostic `postgres-ready` gate Kustomization.
- **aws**: no CNPG `Cluster`. Consumers connect to an external **RDS** instance via `${postgres_host}`
  (+ the existing `${db_port}`) and a site-provided credential Secret. `postgres-ready` is provided
  per-mode (an inert marker on aws), the same way ADR 0035 makes `storage-ready`/MinIO and the
  Traefik edge mode-selected.

The seam is a host cluster-var (`${postgres_host}`) that resolves to the in-cluster service name on-prem
(e.g. `postgresql-cluster-rw.<ns>.svc`) and to the RDS endpoint on aws. The consumers that template the
host (hive-metastore, keycloak, extractor, temporal) resolve it from the cluster-var; superset reads its
DB coordinates from the `superset-env` Secret and launchpad has no Postgres, so for those an aws site sets
the host in the Secret values. Credentials stay fixed-name Secrets (ADR 0031); an aws site materializes
them from RDS instead of from the operator-generated Secret.

**2. Temporal moves off cassandra + Elasticsearch onto PostgreSQL, in both modes.**

Temporal's persistence changes from `{default: cassandra, visibility: elasticsearch}` to
`{default: sql/postgres12, visibility: sql/postgres12}` (the temporalio chart's `sql` datastore). Temporal
gets its own `temporal` + `temporal_visibility` databases and role, provisioned the same way the other app
roles/DBs are (a CNPG managed role on-prem; RDS on aws) and initialized by the chart's schema-setup job
(temporal-sql-tool, via the admin-tools image already pinned). Both datastores resolve to `${postgres_host}`
like every other consumer.

**3. cassandra and Elasticsearch are removed from the deploy base entirely.**

With Temporal on PostgreSQL, nothing consumes them. Drop the `cassandra` + `elasticsearch` bases, their
operators (cass-operator, ECK), and their Flux Kustomizations. This also retires the cassandra rack/PVC
naming and its r1->default rebind migration (ADR/runbook), the cassandra JVM tuning, and the cassandra +
ECK adopt-in-place conflicts (there is no CR to adopt).

## Consequences

- **aws needs one managed service, not three.** RDS only. No OpenSearch, no Amazon Keyspaces. The hardest
  managed swap (Keyspaces) never happens, because cassandra is gone.
- **on-prem drops two operators.** cass-operator and ECK are removed; on-prem runs one stateful operator
  (CNPG) instead of three. This is a change to on-prem as well as aws, so the ADR's scope is both modes.
- **Removes every data-tier adopt-in-place conflict.** postgres in aws has no in-cluster CR; cassandra + ES
  no longer exist. The cutover's only remaining stateful-adoption case is CNPG on-prem.
- **Retires the cassandra rack migration.** Existing r1 clusters (incl WashU prod) no longer owe an
  r1->default PV rebind; cassandra is decommissioned, not migrated in place.
- **New migration cost: Temporal history.** Temporal has no cross-backend history migration. An existing
  cassandra/ES deployment cuts over by quiescing (stop admitting workflows, let running ones drain, resume
  on empty PostgreSQL history) or by accepting completed-history loss. This is tractable for Scout's
  short-lived batch workflows (a maintenance window sized to the longest-running workflow) and is simpler
  than the r1->default PV surgery it replaces. The postgres migration itself is the same one-time
  `pg_dump`/restore at the aws cutover; flipping `${postgres_host}` is the switch.
- **PostgreSQL carries more load.** Temporal history + visibility now share the database with the app
  schemas. Give Temporal its own database + role (isolation, retention), set a sane namespace retention so
  the visibility table does not bloat, and size RDS / the CNPG cluster accordingly. On aws a dedicated
  Temporal RDS instance is optional if load isolation is wanted; at Scout's scale a single instance is fine.
- **Contract changes**: add the Temporal DB/role to `required-vars.txt` (`postgres_host` + `db_port`
  already exist and are reused). Add the RDS credential Secret + the Temporal DB Secret to
  `required-secrets.md`; remove the cassandra + Elasticsearch cluster-vars and their Secrets.
- **TLS is a tier-wide aws item**: extractor, keycloak, hive-metastore, and temporal all connect without
  an explicit sslmode today, so an aws site either leaves `rds.force_ssl` off or adds sslmode across the
  tier (not just Temporal). Out of scope here; called out so it is not missed at the aws cutover.
- **on-prem is otherwise unchanged** (still CNPG-backed; it now also hosts Temporal).

## Alternatives considered

- **Keep self-hosting in aws** (status quo): rejected, operational burden, blast radius, and the cutover
  fragility above.
- **Make aws fully managed with three services** (RDS + OpenSearch + Amazon Keyspaces), keeping Temporal on
  cassandra + ES: rejected. It triples the managed-service surface, and Keyspaces is a hard swap from
  Temporal's cassandra plugin. Because cassandra + ES are Temporal-only, moving Temporal to postgres removes
  the need for two of the three.
- **Move Temporal's default store to postgres but keep ES for visibility**: a valid de-risking phase (drop
  cassandra first, ES second), but the end state carries an entire operator (ECK, or an OpenSearch domain)
  for one job, Temporal visibility, that PostgreSQL serves natively at 1.31. Recommended only as an interim
  step, not the target.
- **Reintroduce ES/OpenSearch later for a non-Temporal search need**: out of scope. If Scout ever grows a
  search feature beyond Temporal visibility, ES/OpenSearch can return as a mode-selected service under the
  same ADR 0035/0036 pattern; nothing here precludes it.
- **Self-host but harden** (Retain reclaim, force-apply, PVC guards): mitigates the data-loss + conflict
  symptoms but keeps the operational burden; strictly worse than a managed endpoint in a cloud that offers
  one. (Still the right hardening for the on-prem CNPG that remains.)
