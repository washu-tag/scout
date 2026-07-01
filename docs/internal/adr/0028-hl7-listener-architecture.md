# ADR 0028: Real-Time HL7 Listener Architecture

**Date:** 2026-07 (proposed 2025-01)
**Status:** Accepted — deployed to production as an **observer / collection** pipeline (see "Scope: observer mode"). The downstream cutover from log-file to event-driven ingest remains future work.
**Decision Owner:** TAG Team

## Context

Scout ingests HL7 radiology reports via nightly log files from an IT-managed file mount. That pipeline:

1. `hl7log-extractor` splits log files on `<SB>`/`<EB>` tags, ZIPs messages, uploads to object storage
2. `hl7-transformer` parses HL7 via a manifest file, writes to Delta Lake
3. incurs hours of latency between report availability and Scout ingestion
4. depends on a file mount that is unreliable and causes frequent ingestion failures

We obtained approval to establish a direct connection to the hospital's HL7 interface engine, giving us `ORU^R01` radiology reports and various ADT messages in near real-time.

### Volume

Example message counts from a recent weekday:

| Message Type | Count/Day | Percentage | Description |
|--------------|----------:|------------|-------------|
| **ORU^R01** | 8,439 | 4% | Radiology reports |
| ADT^A08 | 105,846 | 48% | Patient info updates (while admitted) |
| ADT^A31 | 65,034 | 30% | Patient info updates (not admitted) |
| ADT^A04 | 36,650 | 17% | Outpatient registration |
| ADT^A01 | 2,596 | 1% | Inpatient admission |
| ADT^A28 | 700 | <1% | Add person info |
| ADT^A40 | 299 | <1% | Patient merge |
| **Total** | **219,564** | 100% |  |

These are the message types we requested; we can ask for more or fewer as needed. All messages must be ACKed to prevent upstream queue backup (the interface engine stops sending at a 10,000-message backlog), and we can request replays of missed messages.

### Challenges

1. **Volume** — ~220k messages/day requires organized storage and filtering.
2. **ACK latency** — must ACK promptly to avoid upstream queue backup.
3. **Report updates** — status transitions (Preliminary → Final → Corrected) arrive as repeated messages.
4. **Patient merges** — `ADT^A40` indicates patient-record merges.
5. **Patient demographics** — ADT messages carry demographic data not in `ORU^R01`; scope TBD.
6. **Buffering** — need durable buffering and replay without frequent upstream replay requests.

## Decision

Use **Apache Camel K** for the MLLP listener and batching, with **Kafka (Strimzi)** as a durable buffer. The pipeline is deployed to production, but runs as an **observer** (next section). It is implemented in `ansible/roles/hl7-listener` and deployed via `make install-hl7-listener`; the Camel routes are defined declaratively in that role rather than reproduced here.

Data flow:

```
HL7 Source ──► hl7-listener ──► Kafka ──► hl7-batcher ──► Object Storage (Bronze: hl7-raw)
   (MLLP)      (Camel K)     (hl7-messages) (Camel K)             │
                    │                                             ▼
                    ▼                            Kafka ◄──── (hl7-batches topic: batch manifests)
                   ACK                                            │
                                                                  ▼
                                                    hl7-transformer (NOT wired — future cutover)
                                                                  │
                                                                  ▼
                                                          Delta Lake (Silver)
```

- **hl7-listener** (Camel K) receives MLLP on port 2575, parses the message (HAPI), keys it by HL7 message control ID, writes it to the `hl7-messages` Kafka topic, and ACKs. The ACK is gated on a successful write, so backpressure propagates upstream to the interface engine rather than dropping messages.
- **Kafka** (Strimzi, KRaft) is a durable buffer that decouples ingest rate from downstream processing and enables replay.
- **hl7-batcher** (Camel K) consumes `hl7-messages`, aggregates messages into ZIP files, uploads them to the Bronze object-storage bucket, and publishes each batch's object key to the `hl7-batches` topic. It replaces the batch-zipping the `hl7log-extractor` did for the file-based path.

**Why Camel K:** a Kubernetes operator that runs Camel routes as custom resources; routes are declarative YAML, the operator builds and deploys the container; built-in, production-tested MLLP/HL7/Kafka components; no bespoke Dockerfile or Helm chart. The one cost is that the operator needs a container registry to push the images it builds per Integration.

**Why batching:** individual HL7 messages are small (a few KB). Writing ~220k tiny objects/day to object storage performs poorly (small-file overhead) and inflates S3 API calls/cost. Batching into ZIPs cuts object count while preserving individual message boundaries (messages remain individually extractable). A batch closes when either `completionSize` messages accumulate **or** `completionTimeout` milliseconds elapse with no new message (Camel's inactivity timeout). Size-based (byte) batching has no built-in Camel option and would need a custom `completionPredicate`; not pursued.

## Scope: observer mode

The listener is live in production but deliberately **collect-only**:

- It lands raw HL7 in a dedicated Bronze bucket, **`hl7-raw`** (`s3://hl7-raw/hl7-batches/YYYY/MM/DD/*.zip`), which is **physically separate** from the file-based Bronze (`s3://lake/hl7`) and the Delta Silver lake (`s3://lake/delta`).
- **Nothing downstream consumes `hl7-raw`.** The `hl7-transformer` is not wired to the real-time path; the log-file → Temporal → transformer pipeline remains the active, unchanged ingest.
- Purpose: **observe the real feed** — message mix, volume, timing, and edge cases — to de-risk the eventual cutover from log-file to event-driven ingest.

Everything about downstream *processing* — the transformer trigger, Silver writes, report-update de-duplication, ADT/patient tables, and per-message status tracking — is **out of scope here and deferred to the cutover decision**.

## Implementation (as deployed)

- **Versions:** Strimzi 1.1.0, Kafka 4.3.0 (Strimzi 1.1.0's supported default), Camel K 2.10.1. Pinned exactly in `group_vars/all/versions.yaml` (Renovate/Dependabot drives upgrades).
- **Kafka:** single-broker KRaft (no ZooKeeper), replication factor 1. `hl7-messages` has 3 partitions. Retention is **2 days** with a **`retention.bytes` cap of 2Gi/partition** (~6Gi < the 10Gi PV) so a high-volume feed cannot fill the disk. Kafka is a transport buffer only — Bronze is the durable record — so a short retention window is sufficient.
- **Batching:** `completionSize: 1000`, `completionTimeout: 300000` (5 minutes). Sized for production volume: at ~2–3 msg/sec, size dominates (~1000-message zips, ~200/day), and the timeout flushes partial batches during lulls. (The earlier POC value of `30000` was mis-described as seconds; it is milliseconds. Batch sizing is tunable per environment.)
- **Air-gapped builds:** Camel K pushes built Integration images to the staging Harbor and resolves Maven dependencies through the Nexus `scout-maven` group, trusting the staging CA.
- **Monitoring:** Grafana dashboards (Camel K, Kafka/Strimzi) and Prometheus scrape jobs for the Strimzi operator, Kafka broker, and Camel K integrations, plus dedicated alerts (below).

### Changes to existing Scout components

None to the active pipeline. `hl7log-extractor` and `hl7-transformer` are untouched; Delta Lake, Trino, and Superset are unaffected. The listener stack is additive and opt-in.

## Monitoring

In observer mode, observability is entirely metrics-based (Prometheus/Grafana):

- **Kafka/Strimzi metrics** — broker health, throughput, partition offsets.
- **Camel K route metrics** — per-integration exchange counts, latency, success/failure.
- **Alerts** (Grafana, following the existing `roles/grafana/templates/alerts/` pattern): no HL7 received by the listener; pipeline exchange failures; batcher stalled while the listener is receiving (Kafka lag building); Kafka broker down. Existing node (CPU/memory/disk/iowait) and Temporal alerts cover collateral impact on co-located components.

The current file-based pipeline tracks each message through a Postgres ingest-status database for per-message fate visibility. That per-message tracking is **not** part of observer mode; if/when the real-time path becomes an active ingest, extending the status database to streaming (stages: `received` → `staged` → `ingested`/`failed`) will be part of the cutover design. Writing status before ACK would add latency to the ACK path (and, if Postgres were down, would withhold ACKs — messages would remain queued upstream for retry), so its placement warrants care at that time.

## Alternatives Considered

| Option | Verdict |
|--------|---------|
| **Apache Camel K + Kafka (selected)** | Kubernetes-native, YAML routes, built-in MLLP + Kafka, durable buffer with replay |
| `hl7-to-kafka` (or fork) | Code to write/maintain; must publish images and a Helm chart |
| `python-hl7` MLLP → Kafka | More code to write/maintain; must publish images and a Helm chart |
| Direct to object storage | Too many small writes |
| Listener → file system | Too many small writes |

## Future work (cutover — out of scope for observer mode)

The following are recorded for the eventual event-driven cutover; none are implemented here.

**Transformer trigger.** Something must drive the `hl7-transformer` from the `hl7-batches` manifests. Two candidates: (a) a **Temporal trigger** — a Kafka consumer / Camel K route / Temporal scheduled job kicks off the existing workflow (low effort, low risk, reuses today's batch-oriented transformer); or (b) **Spark Structured Streaming** — a long-running job consumes Kafka directly and writes Delta, removing Temporal from the real-time flow (lower latency, but a significant refactor and higher risk). Recommendation: spike Structured Streaming to gauge effort/risk; fall back to a Temporal trigger otherwise. (A plain Kubernetes CronJob → Temporal is *not* preferred — Temporal's own scheduled jobs are the better fit if Temporal stays in the stack.)

**Message-type handling.** `ORU^R01` → Delta Lake, later handling status updates (P→F→C). ADT messages stored raw (with `message_dt` for ordering) for future processing; `ADT^A40` (merges) will need a patient-ID mapping table; other ADT types may inform a demographics table pending research into their contents. Note `ORU^R01` carries patient demographics in its PID segment, so ADT is not required for basic patient info.

**Report-update de-duplication.** The same report arrives repeatedly as its status changes (Preliminary → Final → Corrected). A "latest version" view is being developed separately from the listener; a Delta Lake `MERGE` at the transformer would also absorb at-least-once duplicates from Kafka replay.

**High availability.** Kafka RF, listener/batcher replicas, and transformer HA are all deferred; the observer runs single-instance. Kafka's buffering plus upstream replay is the current safety net for on-prem outages.

**Cloud / hybrid deployment.** Running the listener (and possibly Kafka/batcher) in the cloud with a site-to-site VPN back to on-prem Scout would improve availability over the periodically-unreliable on-prem cluster. This requires IT involvement for the VPN and adds cross-cluster tracking complexity; to be evaluated as a separate spike.

## Open Questions

Resolved by this deployment: Kafka topics/partitions/retention (decided — see Implementation); container registry (Camel K pushes to the staging Harbor, air-gapped build wired); test environment (live on the `washu-4` dev cluster against the hospital test feed); ADT message contents (being characterized now, which is the point of observer mode).

Still open:

- [ ] `hl7-transformer` trigger mechanism — which option to pursue (Temporal trigger vs Spark Structured Streaming).
- [ ] Patient-info/demographics table schema — what fields are needed beyond the `ORU^R01` PID segment.
- [ ] `ADT^A40` (merge) handling — patient-ID mapping approach.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Camel MLLP edge cases | Low | High | Observer mode surfaces real-message edge cases before cutover |
| Kafka operational complexity | Medium | Medium | Single-broker buffer with capped retention; document runbooks |
| On-prem infrastructure outages | Medium | High | Kafka buffering; request upstream replay as last resort |
| `ADT^A40` merge complexity | High | Medium | Defer to a later phase; research before implementing |

## References

- [Apache Camel K](https://github.com/apache/camel-k)
- [Apache Camel MLLP Component](https://camel.apache.org/components/4.8.x/mllp-component.html)
- [Apache Camel Kafka Component](https://camel.apache.org/components/4.8.x/kafka-component.html)
- [Strimzi Kafka Operator](https://strimzi.io/)
- [Spark Structured Streaming + Kafka](https://spark.apache.org/docs/latest/structured-streaming-kafka-integration.html)
- [Delta Lake Streaming](https://docs.delta.io/latest/delta-streaming.html)
- [MLLP Protocol Specification](https://rhapsody.health/resources/mlp-minimum-layer-protocol/)
- [HL7 v2.x Message Types](https://hl7-definition.caristix.com/v2/HL7v2.7/TriggerEvents)
