# ADR 0011: Real-Time HL7 Listener Architecture

**Date:** 2025-01-09
**Status:** Proposed
**Decision Owner:** TAG Team

## Context

Scout ingests HL7 radiology reports via nightly log files from an IT-managed file mount. The current pipeline:

1. `hl7log-extractor` splits log files on `<SB>`/`<EB>` tags, ZIPs messages, uploads to object storage
2. `hl7-transformer` parses HL7 via manifest file, writes to Delta Lake
3. Results in latency of hours between report availability and Scout ingestion
4. The file mount is unreliable, causing frequent ingestion failures

We have approval to establish a direct HL7 listener connection to the hospital's HL7 interface engine giving us access to ORU^R01 radiology reports and various ADT messages in near real-time.

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

These message types are what we requested and we could ask for more or fewer types as needed.

We must ACK all messages to prevent queue backup (10,000 message limit before interface engine stops sending). We can also request replays of missed messages if needed.

### Challenges

1. **Volume**: ~220k messages/day requires organized storage and filtering
2. **ACK Latency**: Must ACK promptly to avoid upstream queue backup
3. **Report Updates**: Handle status transitions (Preliminary → Final → Corrected)
4. **Patient Merges**: Handling ADT^A40 messages indicate patient record merges
5. **Patient Demographics**: ADT messages contain demographic data not in ORU^R01; scope TBD
6. **Buffering**: Need message buffering and replay capability without frequent upstream requests for replays

## Decision

Use Apache Camel K for MLLP listener and message batching, with Kafka as the durable message buffer. The hl7-batcher (Camel K) replaces the batch zipping functionality previously used in the hl7log-extractor.

### Apache Camel K

Use **Apache Camel K** for the MLLP listener:

- [Camel K](https://github.com/apache/camel-k) is a Kubernetes operator that deploys Camel routes as custom resources
- Define integration as YAML, operator handles container build and deployment
- Built-in MLLP and Kafka components (production-tested)
- No Dockerfile, no Helm chart needed, but does require container registry to push build Integration CRD images

### hl7-listener Integration

Receives MLLP messages, writes to Kafka, ACKs immediately. Example route:

```yaml
apiVersion: camel.apache.org/v1
kind: Integration
metadata:
  name: hl7-listener
spec:
  dependencies:
    - camel:mllp
    - camel:hl7
    - camel:kafka
  flows:
    - route:
        id: hl7Listener
        from:
          uri: "mllp://0.0.0.0:2575"
          steps:
            - unmarshal:
                hl7: {}
            - setHeader:
                name: kafka.KEY
                expression:
                  header: CamelHL7MessageControl
            - to:
                uri: "kafka:hl7-messages?brokers={{kafka.brokers}}"
```

### Middleware

Use **Kafka** as durable buffer between listener and extractor:

- Listener writes to Kafka and ACKs immediately (fast ACK, decoupled from downstream)
- 7-day retention enables replay without requesting from upstream
- Use [Strimzi Kafka Operator](https://strimzi.io/) to deploy/manage Kafka in-cluster

### hl7-batcher Integration

**Why batching?** Individual HL7 messages are small (typically a few KB). Writing ~220k individual files per day to object storage creates:
- Poor filesystem performance with lots of small files
- Excessive S3 API calls, more per-object overhead and cost

Batching aggregates messages into ZIP files, reducing file count while preserving individual message boundaries. Users can still extract individual HL7 messages from ZIPs as needed.

**Batching configuration:**

The POC demonstrates two options for batch completion:

| Parameter | Example Value | Description |
|-----------|---------------|-------------|
| `completionSize` | 100 | Batch closes after N messages |
| `completionTimeout` | 30000 | Batch closes after N seconds of **inactivity** (no new messages) |

There was a suggestion for batching by file size. There is no clear built-in Camel option for size-based batching. Implementing this would require custom Camel `completionPredicate` to track aggregated size. This was not tested in the POC but should be considedred. The exact batching stragegy is adjustable later.

** hl7-batcher Integration example: **

Consumes from Kafka, batches messages into ZIPs, uploads to S3, publishes manifest to Kafka:

```yaml
apiVersion: camel.apache.org/v1
kind: Integration
metadata:
  name: hl7-batcher
spec:
  dependencies:
    - camel:kafka
    - camel:aws2-s3
    - camel:zipfile
  traits:
    mount:
      configs:
        - 'secret:hl7-batcher-s3-creds'
  flows:
    - beans:
        - name: zipAggregator
          type: '#class:org.apache.camel.processor.aggregate.zipfile.ZipAggregationStrategy'
          constructors:
            0: false  # preserveFolderStructure
            1: true   # useFilenameHeader
    - route:
        id: batchHl7Messages
        from:
          uri: "kafka:hl7-messages?brokers={{kafka.brokers}}&groupId=hl7-batcher"
          steps:
            - setHeader:
                name: CamelFileName
                expression:
                  simple: '${header.kafka.KEY}-${date:now:yyyyMMddHHmmssSSS}.hl7'
            - aggregate:
                completionSize: 100
                completionTimeout: 30000
                aggregationStrategy: '#zipAggregator'
                correlationExpression:
                  constant: 'true'
                steps:
                  - setHeader:
                      name: CamelAwsS3Key
                      expression:
                        simple: 'hl7-batches/${date:now:yyyy/MM/dd}/batch-${date:now:HHmmssSSS}.zip'
                  - to:
                      uri: "aws2-s3://hl7-raw?..."
                  - to:
                      uri: "kafka:hl7-batches?brokers={{kafka.brokers}}"
```

### Processing Integration

- **hl7-batcher** (Camel K) batches messages into ZIPs, uploads to object storage (Bronze)
- **hl7-batches** Kafka topic receives S3 paths for each uploaded batch
- **hl7-transformer** parses HL7, writes to Delta Lake (Silver) - trigger mechanism TBD
- Delta Lake, Trino, Superset unchanged
- **hl7log-extractor** retained for legacy file-based ingestion (optional)

### Message Type Strategy

| Message | v1 | v2 | v3+ |
|---------|----|----|-----|
| ORU^R01 | Process → Delta Lake | Handle updates (P→F→C) | |
| ADT^A40 | Store raw to `adt_messages` table | | Process → patient ID mapping table |
| Other ADT | Store raw to `adt_messages` table | | Patient info table (TBD) |

ORU messages contain embedded patient demographics (PID segment), so ADT messages are not required for basic patient info.

ADT messages stored in v1 with `message_dt` timestamp to preserve ordering for future processing. ADT^A40 (merges) will require a patient ID mapping table to maintain accurate historical queries. Other ADT messages may inform a dedicated patient or demographic table in the future; further research needed on exact contents of each ADT type.

## Alternatives Considered

| Option | Verdict |
|--------|---------|
| **Apache Camel K + Kafka (selected)** | Kubernetes-native, YAML-based routes, built-in MLLP + Kafka, durable buffer with replay |
| hl7-to-kafka (or fork) | Some code to write/maintain, need to publish images and create helm chart |
| python-hl7 mllp to Kafka | More code to write/maintain, need to publish images and create helm chart |
| Direct to object storage | Too many small writes |
| Listener to file system | Too many small writes |

### Cloud Deployment

Running the HL7 listener in the cloud (e.g., Azure) would provide durability benefits:

- **Higher availability** than on-prem K3s cluster which experiences periodic outages
- **Isolated infrastructure** reduces risk of local network issues affecting HL7 ingestion

However, the current air-gapped deployment prevents direct cloud connectivity. A **VPN** would be required to enable this architecture.

**Network architecture options:**

| Option | Description | Complexity |
|--------|-------------|------------|
| **Fully on-prem** | All components run on-prem K3s | Low |
| **Hybrid (cloud listener)** | hl7-listener + Kafka in cloud, VPN to on-prem Scout | Medium |
| **Hybrid (cloud listener + batcher)** | hl7-listener + Kafka + batcher in cloud, VPN to on-prem Scout | Medium-High |

**VPN requirements for hybrid deployment:**

If pursuing cloud deployment, a site-to-site VPN would need to connect:
- Cloud hl7-listener ← HL7 interface engine for MLLP traffic
- Cloud Kafka/S3 → On-prem Scout (for hl7-transformer to read batches, grafana/prometheus to read metrics, postgres for status tracking)

**Considerations:**
- Adds operational complexity
- Need small amount of IT involvement to establish/maintain VPN
- Tracking messages across network boundary and clusters adds complexity
- Should we batch message on-prem or in cloud? This needs evaluation.

**Recommendation:** Two pronged approach, first implement core Camel K + Kafka architecture on-prem. In parallel, work on cloud deployment spike (VPN and additional Ansible/Terraform(?)) to evaluate feasibility. If successful, consider moving listener and/or batcher to cloud before moving to test environment.

## Architecture

### Component Overview

```
HL7 Source ──► hl7-listener ──► Kafka ──► hl7-batcher ──► Object Storage (Bronze)
   (MLLP)      (Camel K)     (hl7-messages) (Camel K)           │
                    │                                            ▼
                    ▼                         Kafka ◄──── (hl7-batches topic)
                   ACK                  (batch manifests)        │
                                                                 ▼
                                                          hl7-transformer
                                                           (trigger TBD)
                                                                 │
                                                                 ▼
                                                          Delta Lake (Silver)
```

| Component | Type | Lifecycle | Purpose |
|-----------|------|-----------|---------|
| hl7-listener | Camel K Integration | Runs continuously | Receive MLLP, write to Kafka, ACK |
| Kafka | Message broker (Strimzi) | Runs continuously | Durable buffer, 7-day retention |
| hl7-batcher | Camel K Integration | Runs continuously | Batch messages into ZIPs, upload to S3 (Bronze), publish manifest |
| hl7-transformer | Python/Spark | TBD | Parse HL7, write to Delta Lake (Silver) |

### Changes to Existing Scout Components

| Component | Change Required |
|-----------|-----------------|
| hl7log-extractor | No changes needed for real-time flow; retained for legacy file-based ingestion |
| hl7-transformer | Trigger mechanism TBD (see hl7-transformer Integration section); write ADT messages to `adt_messages` table |
| Delta Lake schema | Add `adt_messages` table for raw ADT storage; future phases add patient_id_mapping table |
| Ingest status database | Adapt schema for streaming |

### Monitoring and Pipeline Tracking

The current Scout deployment uses a postgres ingest status database to track individual messages through pipeline stages. This provides Grafana dashboard visibility into message processing status and failures.

For the streaming architecture, we will adapt this approach:

**Pipeline stages to track:**
| Stage | Component | Description |
|-------|-----------|-------------|
| `received` | hl7-listener | Message received via MLLP, written to Kafka |
| `staged` | hl7-batcher | Message uploaded to S3 |
| `ingested` | hl7-transformer | Message written to Delta Lake |
| `failed` | Any | Processing failed with error details |

**Observability stack:**
- **Postgres**: Individual message tracking, error details, Grafana dashboards
- **Prometheus**: Kafka metrics via Strimzi (lag, throughput, partition offsets)
- **Prometheus**: Camel K route metrics (message counts, latencies, success/failure rates)
- **Grafana**: Dashboards query both postgres (message fate) and Prometheus (system health)

**Implementation consideration:** The hl7-listener could write `received` status to postgres before or after writing to Kafka and sending ACK. Writing to postgres before Kafka adds latency to the ACK path. If postgres is unavailable, the route fails and no ACK is sent—the message remains in the upstream queue for retry. This behavior may be desirable (don't ACK what we can't track).

### hl7-transformer Integration

The POC hl7-batcher publishes batch manifest paths to Kafka (`hl7-batches` topic). A mechanism is needed to trigger the hl7-transformer to process these batches.

**Options:**

| Option | Description | Effort | Risk |
|--------|-------------|--------|------|
| **Spark Structured Streaming** | Transformer runs continuously, consumes directly from Kafka | Med/High (refactor) | High |
| **Temporal trigger** | Kafka consumer or Camel K route triggers Temporal workflow | Low | Low |
| **Kubernetes CronJob** | Scheduled job polls for new batches | Low | Low |

- **Spark Structured Streaming** eliminates Temporal from the real-time flow entirely. The transformer becomes a long-running job that continuously consumes from Kafka and writes to Delta Lake. This offers lower latency but requires significant refactoring of the current batch-oriented transformer code.

**Recommendation:** Spike on Spark Structured Streaming to evaluate effort and risk. If feasible, this provides the cleanest real-time architecture. If not, implement a Temporal trigger with minimal changes to existing transformer code.

## Data Model Considerations

This section identifies some data modeling challenges we will encounter.

### Known Challenges

**Report Updates:** The same report arrives multiple times as its status changes:

```
08:00  ORU^R01  Report 123  Status: P (Preliminary)
08:30  ORU^R01  Report 123  Status: F (Final)
09:00  ORU^R01  Report 123  Status: C (Corrected)
```

**Patient Merges:** ADT^A40 messages indicate two patient records have been merged:

```
ADT^A40: Patient 78901 merged into Patient 78900
→ Historical reports filed under 78901 become orphaned
→ Query for Patient 78900 misses historical data
```

### Preliminary Approach

**Phase 1 (v1):** Ingest all ORU^R01 messages as new rows. Users may see multiple versions of the same report (Preliminary, Final, Corrected). Store ADT messages for future processing. Close to what we currently do except with new table for ADT messages to be processed later.

**Phase 2 (v2):** Handle report updates. We are currently working on adding a view that surfaces the "latest" version. This is being developed seperately from the real-time listener.

**Phase 3 (v3):** Handle patient merges from ADT^A40. Likely requires a patient ID mapping table to link old MRNs to surviving MRNs.

**Future:** Dedicated patient demographics table sourced from ADT messages (A08, A31, A04, A01, A28). Scope TBD pending research into message contents.

## Production Considerations

### High Availability

| Component | HA Strategy |
|-----------|-------------|
| **Kafka** | TBD |
| **hl7-listener** | TBD |
| **hl7-batcher** | TBD |
| **hl7-transformer** | Depends on trigger mechanism; Spark Structured Streaming or Temporal provide their own HA |

### Monitoring & Alerting

- **Kafka lag monitoring** - Alert if consumer falls behind (indicates processing bottleneck)
- **hl7-listener health** - Camel K provides `/health` endpoint; integrate with K8s probes
- **S3 upload metrics** - Track success/failure rates, latency
- **End-to-end latency** - Track time from message receipt to Delta Lake availability
- **Integration with Grafana/Prometheus** - Use existing Scout monitoring stack

### Failure Recovery

| Scenario | Recovery |
|----------|----------|
| **hl7-listener restart** | Kafka durability; no message loss if written before ACK |
| **hl7-batcher restart** | Aggregation state lost, but consumer resumes from last committed offset |
| **S3 upload failure** | Retry logic in Camel route; idempotent uploads (same key overwrites) |
| **hl7-transformer failure** | Depends on trigger; Delta Lake MERGE handles duplicate processing |
| **Kafka broker failure** | Strimzi handles failover; 7-day retention enables replay |

### Scaling

- **Current volume**: ~2-3 msg/sec sustained, ~40 msg/sec burst - well within single instance capacity
- **Kafka partitions** control parallelism for consumers
- **Multiple hl7-listener replicas** for throughput (if needed)
- **hl7-batcher scaling** via Kafka consumer groups (one consumer per partition)

### Security

- **Kafka TLS** - Strimzi supports TLS for client connections
- **S3 credentials** - Kubernetes secrets mounted via Camel K `mount.configs` trait
- **Network policies** - Restrict pod-to-pod communication to required paths

### Container Registry

Camel K operator requires a container registry to push built Integration images:

| Option | Description | Deployment |
|--------|-------------|------------|
| **ttl.sh** | Anonymous ephemeral registry (POC default) | ❌ Not for production, useful for testing |
| **Harbor on staging** | Use existing staging Harbor | ❌ prod can reach staging |
| **Local registry** | IT managed internal registry | May already exists |
| **Cloud registry (ACR)** | Azure Container Registry or similar | Would need to be on cloud |
| **Pre-built images** | Build images externally, deploy as Deployments | Loses Camel K operator benefits |

### Backup & Disaster Recovery

- **Kafka retention** - 7 days of message history for replay
- **S3/MinIO** - Backed up per existing Scout policies
- **Delta Lake** - Versioning provides point-in-time recovery

### Automated Testing

We will need a variety of automated tests to validate the hl7-listener architecture:

| Test Type | Scope | Environment |
|-----------|-------|-------------|
| **Unit tests** | Camel route logic, message parsing | Local/CI |
| **Integration tests** | hl7-listener → Kafka → hl7-batcher flow | Test Kafka cluster |
| **End-to-end tests** | Full flow with synthetic HL7 messages | Dev K8s cluster |
| **Load tests** | Verify performance at expected volumes | Dev K8s cluster |
| **Failure tests** | Component restarts, network partitions | Dev K8s cluster |

Test infrastructure:
- **hl7-test-sender (POC)** (Camel K) generates synthetic HL7 messages for testing
- Dedicated test Kafka topics to isolate from production
- Assertions on Kafka message counts, S3 object presence, Delta Lake row counts

## Open Questions

- [ ] Container registry for Camel K - can production reach Harbor on staging? Need local registry?
- [ ] ADT message contents - what exactly is in A08, A31, A04, A01, A28?
- [ ] Patient info table schema - what fields are needed beyond what ORU PID segment provides?
- [ ] Test environment - when available? how to schedule testing?
- [ ] Kafka configuration - what topics, how many partitions, retention settings?
- [ ] hl7-transformer trigger mechanism - which option to pursue?

## Test Plan

### Goal

Validate end-to-end message flow before production deployment.

### POC Validation (Complete)

- [x] Deploy Camel K operator to cluster
- [x] Deploy Strimzi Kafka operator and cluster
- [x] Create Kafka topics: `hl7-messages`, `hl7-batches`
- [x] Apply `hl7-listener` Integration CR
- [x] Apply `hl7-batcher` Integration CR
- [x] Apply `hl7-test-sender` Integration CR (for testing)
- [x] Validate message flow: listener → Kafka → batcher → S3 → Kafka manifest

### Internal Testing (Pending)

- [ ] Automated unit tests for Camel K integrations
- [ ] Integration tests with test Kafka cluster
- [ ] End-to-end tests with synthetic HL7 messages
- [ ] Failure scenario tests (component restarts, network issues)
- [ ] Performance/load testing with expected message volumes

### Hospital Test Environment (Pending)

- [ ] Connect hl7-listener to clinical test environment HL7 feed
- [ ] Validate message flow with real HL7 messages:
  - Messages received and ACKs sent correctly
  - No upstream queue backup
  - Messages appear in Kafka with correct parsing
  - Batcher creates ZIPs with proper structure
  - ZIPs written to object storage
- [ ] Validate end-to-end to Delta Lake (requires hl7-transformer integration)
- [ ] Test failure scenarios:
  - Listener restart - verify no message loss (Kafka durability)
  - Batcher restart - verify Kafka replay works
  - Transformer failure - verify retry mechanism works

### Success Criteria

- All messages ACK'd promptly (no upstream queue backup)
- ORU data lands in Delta Lake with correct parsing
- System recovers from component restarts without data loss
- End-to-end latency within acceptable range (minutes, not hours)

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Camel MLLP edge cases | Low | High | Thorough testing with real messages; fallback to hl7-to-kafka (or fork) |
| Kafka operational complexity | Medium | Medium | Document runbooks |
| On-prem infrastructure outages | Medium | High | Kafka provides buffering; request replay as last resort |
| ADT^A40 merge complexity | High | Medium | Defer to v3; research thoroughly before implementing |

## Implementation Phases

| Phase | Scope | Status |
|-------|-------|--------|
| **0: Design** | Finalize architecture | ✅ Complete |
| **1: POC** | Camel K listener + batcher, Kafka, S3 integration | ✅ Complete |
| **2: hl7-transformer Integration** | Decide and implement Kafka → hl7-transformer trigger | Pending |
| **3: Internal Testing** | Automated tests for hl7-listener components | Pending |
| **4: Clinical Test Environment** | Connect to clinical test environment HL7 feed | Pending |
| **5: Production** | Production connection, monitoring, alerting | Pending |
| **6: Updates (v2)** | Handle report status updates with `reports_latest` view | Pending |
| **7: Merges (v3)** | Patient ID mapping table for ADT^A40 processing | Pending |
| **8: Patient Info** | Dedicated patient demographics table from ADT messages | Pending |

## References

- [Apache Camel K GitHub](https://github.com/apache/camel-k)
- [Apache Camel MLLP Component](https://camel.apache.org/components/4.8.x/mllp-component.html)
- [Apache Camel Kafka Component](https://camel.apache.org/components/4.8.x/kafka-component.html)
- [Strimzi Kafka Operator](https://strimzi.io/)
- [Spark Structured Streaming + Kafka](https://spark.apache.org/docs/latest/structured-streaming-kafka-integration.html)
- [Delta Lake Streaming](https://docs.delta.io/latest/delta-streaming.html)
- [hl7-to-kafka](https://github.com/diz-unimr/hl7-to-kafka)
- [python-hl7 MLLP](https://python-hl7.readthedocs.io/en/latest/mllp.html)
- [MLLP Protocol Specification](https://rhapsody.health/resources/mlp-minimum-layer-protocol/)
- [HL7 v2.x Message Types](https://hl7-definition.caristix.com/v2/HL7v2.7/TriggerEvents)
