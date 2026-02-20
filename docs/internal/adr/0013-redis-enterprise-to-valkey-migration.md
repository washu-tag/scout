# ADR 0013: Migration from Redis Enterprise Cluster to Valkey

**Date**: 2026-02  
**Status**: Proposed  
**Decision Owner**: TAG Team

## Context

### Current State

Scout uses Redis Enterprise Cluster (REC) for in-memory data storage. REC is a commercial product from Redis Ltd. deployed via a Kubernetes operator pattern: a Redis Enterprise Operator, a three-node RedisEnterpriseCluster custom resource (the minimum REC allows for high availability), and individual RedisEnterpriseDatabase custom resources for each consuming service.

Three Scout services use Redis:

| Service | Purpose | Redis Features Used |
|---------|---------|---------------------|
| **OAuth2 Proxy** | HTTP session storage for authenticated users | Key-value GET/SET with TTL |
| **Superset** | Query/dashboard cache and Celery async task broker | Key-value with TTL, list-based FIFO queuing (LPUSH/BRPOP) |
| **Open WebUI** | WebSocket coordination across pod replicas | Pub/sub, key-value for distributed session state |

All three use cases store **ephemeral data**. If Redis data is lost, OAuth2 Proxy users re-authenticate via Keycloak SSO, Superset caches rebuild on the next query, and Open WebUI chat sessions briefly disconnect and auto-reconnect. There is no persistent application data in Redis.

### Problem

The Redis Enterprise license has expired on development deployments, preventing further modifications. REC requires a paid commercial license beyond its trial period. Scout's Redis usage is limited to basic operations (key-value, pub/sub, list queuing) that are fully supported by open-source alternatives, making the commercial license an unnecessary cost.

Beyond licensing, the REC deployment model imposes overhead disproportionate to Scout's needs:

| Resource | Redis Enterprise Cluster |
|----------|-------------------------|
| Pods | 4 (1 operator + 3 cluster nodes) |
| Memory limit | 12.5Gi (512Mi operator + 4Gi per node) |
| CPU limit | 6.5 cores (500m operator + 2 per node) |
| Storage | 24Gi (8Gi per node) |
| CRDs | 2 (RedisEnterpriseCluster, RedisEnterpriseDatabase) |

The three-node cluster exists only because REC mandates it for high availability — Scout does not require Redis HA given that all data is ephemeral. The operator and custom-resource machinery adds operational complexity (CRD management, operator upgrades, per-service database provisioning) for features Scout does not use.

Additionally, REC does not support the `SELECT` command for logical database separation, which means Superset's cache and Celery broker must share a single logical database.

### Relationship to ADR 0011

ADR 0011 introduced service-mode variables for deployment portability, including `redis_mode` with options `enterprise`, `standalone`, and `external`. This migration implements a concrete change to the default value of `redis_mode` — from `enterprise` (Redis Enterprise Cluster) to `standalone` (Valkey) — using the pattern ADR 0011 established.

## Decision

Replace Redis Enterprise Cluster with **Valkey**, deployed as a single standalone instance via its official Helm chart.

Valkey is an open-source, high-performance key-value datastore forked from Redis OSS 7.2.4 in March 2024, after Redis Ltd. changed the Redis license from BSD to a restrictive dual license (RSALv2/SSPLv1). Valkey is governed by the Linux Foundation under the BSD 3-clause license, with contributions from approximately 50 companies including AWS, Google Cloud, Oracle, Ericsson, and Snap.

### Why Valkey Is Suitable

**Protocol compatibility**: Valkey implements the complete Redis RESP protocol (RESP2 and RESP3). All standard Redis commands work identically. Connection URLs continue to use the `redis://` scheme. No application code changes are needed — only connection hostnames and ports change in Ansible configuration.

**Client library compatibility**: All Redis client libraries used by Scout's services (Go Redis client in OAuth2 Proxy, Python redis-py in Superset, Python/Node.js clients in Open WebUI) work with Valkey without modification.

**Feature coverage**: Every Redis feature Scout uses is fully supported:

| Feature | Redis Enterprise | Valkey |
|---------|-----------------|--------|
| Key-value GET/SET | Yes | Yes |
| TTL expiry | Yes | Yes |
| List operations (Celery) | Yes | Yes |
| Pub/Sub | Yes | Yes |
| AOF persistence | Yes | Yes |
| Password authentication | Yes | Yes |
| Logical databases (SELECT) | **No** | **Yes** |

**Resource efficiency**: A single Valkey pod replaces the four-pod REC deployment:

| Resource | Redis Enterprise | Valkey | Reduction |
|----------|-----------------|--------|-----------|
| Pods | 4 | 1 | 75% |
| Memory limit | 12.5Gi | 2Gi | 84% |
| CPU limit | 6.5 cores | 1 core | 85% |
| Storage | 24Gi | 8Gi | 67% |
| CRDs | 2 | 0 | Eliminated |
| License cost | Commercial | Free | Eliminated |

### Migration Approach

Because all Redis data in Scout is ephemeral, the migration is a clean cutover with no data migration required. Valkey is deployed alongside the existing REC, consuming services are reconfigured to point at Valkey, and the REC resources are removed. The only user-visible impact is a one-time re-authentication (OAuth2 Proxy sessions are cleared).

## Alternatives Considered

### KeyDB

[KeyDB](https://github.com/Snapchat/KeyDB) is a multithreaded fork of Redis created by Snap Inc. Its main differentiator is true multithreading — a single instance can use multiple CPU cores.

**Rejected**: The last release was v6.3.3 in May 2023. The project appears dormant — critical bug reports have gone unaddressed and there has been no maintainer activity. It is based on Redis 6.x and lacks RESP3 support. Single-company governance (Snap) with no safeguards against abandonment. Scout's modest workload does not benefit from multithreading.

### Dragonfly

[Dragonfly](https://github.com/dragonflydb/dragonfly) is a ground-up reimplementation of a Redis- and Memcached-compatible datastore using a shared-nothing, multi-threaded architecture designed for modern hardware.

**Rejected**: Licensed under BSL 1.1 (Business Source License), which is not open source. More critically, Dragonfly does not support AOF persistence — only snapshot-based persistence, which means data loss between snapshots is possible. Celery compatibility is being actively validated but is not yet fully proven. Single-company governance (DragonflyDB Inc.).

### Garnet

[Garnet](https://github.com/microsoft/garnet) is a Redis-compatible remote cache-store from Microsoft Research, built on .NET, targeting sub-300us P99.9 latency.

**Rejected**: Garnet explicitly states it is "not intended to be a 100% perfect drop-in replacement for Redis." Its persistence uses a proprietary format (not Redis-compatible RDB/AOF). Celery broker compatibility is unverified. Requires the .NET runtime. The Kubernetes Helm chart ecosystem is still immature.

### Apache Kvrocks

[Kvrocks](https://kvrocks.apache.org/) is an Apache-incubating, Redis-compatible NoSQL database that stores data on disk via RocksDB rather than in memory.

**Rejected**: Kvrocks is fundamentally a disk-based store, not an in-memory store. Latency is inherently higher than Redis/Valkey for caching and session storage workloads. Pub/sub support is partial. Celery broker compatibility is unverified. Kvrocks solves a different problem (large datasets on disk) than Scout's use case (low-latency ephemeral caching).

### Redict

[Redict](https://redict.io/) is a copyleft fork of Redis 7.2.4, created by Drew DeVault under the LGPL-3.0 license, prioritizing stability over new features.

**Rejected**: Redict has no Kubernetes Helm chart — deployment manifests would need to be created from scratch. The project has a small community (primarily one maintainer) and low development activity. The LGPL license may raise concerns for some organizations.

### Redis Community Edition (Redis Open Source)

[Redis](https://redis.io/) is the original project, now maintained by Redis Ltd. After the March 2024 license change from BSD to RSALv2/SSPLv1, Redis 8.0 (May 2025) added AGPLv3 as a third licensing option.

**Not selected**: Redis remains the reference implementation with the broadest compatibility — it is Celery's primary tested broker. However, the license situation is complex: users must choose between AGPLv3 (copyleft), RSALv2, or SSPLv1 (neither of which are OSI-approved). Two license changes in two years raises concerns about long-term stability. Single-company governance (Redis Ltd.) provides no safeguards against future changes. The Bitnami Helm chart now requires a commercial subscription for images.

### Comparative Summary

| Criterion | Valkey | KeyDB | Dragonfly | Garnet | Kvrocks | Redict | Redis CE |
|-----------|--------|-------|-----------|--------|---------|--------|----------|
| License | BSD-3 | BSD-3 | BSL 1.1 | MIT | Apache 2.0 | LGPL-3.0 | AGPL/RSALv2/SSPLv1 |
| Governance | Linux Foundation | Snap Inc. | DragonflyDB Inc. | Microsoft | Apache SF | BDFL | Redis Ltd. |
| Celery compatible | Yes | Yes | Active testing | Unverified | Unverified | Yes | Yes (primary) |
| AOF persistence | Yes | Yes | **No** | Own format | N/A (RocksDB) | Yes | Yes |
| Official Helm chart | Yes | No | Yes | Basic | Operator-based | **None** | Bitnami (paid) |
| Project activity | Very high | **Stale** | Very high | Very high | High | Low | Very high |

For Scout's requirements, Celery broker compatibility eliminates Garnet and Kvrocks, absence of AOF persistence eliminates Dragonfly, lack of a Helm chart eliminates Redict, and project dormancy eliminates KeyDB. This leaves Valkey and Redis CE. Valkey's permissive BSD-3 license, multi-vendor Linux Foundation governance, and active innovation (Valkey 8.0 demonstrated a 230% throughput improvement over the 7.2 baseline) make it the stronger choice over Redis CE's uncertain licensing trajectory.

## Consequences

### Positive

- Eliminates commercial licensing cost and expired-license deployment blockers
- Reduces resource footprint by approximately 80% (1 pod vs 4, 2Gi vs 12.5Gi memory)
- Removes operator and CRD management overhead
- Simplifies the deployment model: one Helm chart replaces an operator, cluster CR, and per-service database CRs
- Gains logical database support (`SELECT`), allowing optional separation of Superset's cache and Celery broker
- BSD-3 license with Linux Foundation governance provides long-term licensing stability
- Aligns with ADR 0011's service-mode pattern — `redis_mode: standalone` becomes the default for on-premise deployments

### Negative

- One-time user disruption during migration: users must re-authenticate and Superset caches are cleared
- Single-instance deployment has no built-in redundancy (acceptable given ephemeral data; can be revisited if HA is needed in the future)
- Valkey is younger than Redis as an independent project (forked March 2024), though it inherits Redis's mature codebase

### Risks and Mitigations

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Client library incompatibility | Low | Valkey uses identical RESP protocol; all clients work as-is |
| Missing features | Low | All Scout-used features verified present in Valkey |
| Performance regression | Low | Valkey 8.0 benchmarks show improved throughput over the Redis 7.2 baseline |
| Air-gapped deployment | Low | Ensure Valkey container image is available in Harbor proxy registry |

## References

- [Valkey Project](https://valkey.io/)
- [Valkey GitHub](https://github.com/valkey-io/valkey)
- [Valkey Helm Chart](https://github.com/valkey-io/valkey-helm)
- [Valkey Migration Guide](https://valkey.io/topics/migration/)
- [Linux Foundation Launches Valkey](https://www.linuxfoundation.org/press/linux-foundation-launches-open-source-valkey-community)
- [ADR 0011: Deployment Portability via Layered Architecture](0011-deployment-portability-layered-architecture.md)
