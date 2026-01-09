# ADR 0011: Real-Time HL7 Listener Architecture

**Date:** 2025-01-07
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

We must ACK all messages to prevent queue backup (10,000 message limit before interface engine stops sending).

### Challenges

1. **Volume Management:** Will be overwhelmed by messages - need organized storage and filtering strategy
2. **Reliability:** Must ACK promptly, 10k message limit before they stop sending
3. **Database End State:** How do report updates (Preliminary → Final → Corrected) and patient merges (ADT^A40) get handled and represented to users?
4. **Infrastructure Reliability:** Current on-prem K3s cluster can be unreliable, need buffering and replay strategy. We can ask for upstream HL7 message replays if needed, but would like to avoid frequent requests.
5. **Object Storage Performance:** Direct per-message writes to object storage performed poorly (learned from previous Temporal experience) - need to keep batching strategy.

## Decision

Use Apache Camel K for MLLP listener that writes HL7 messages to Kafka, then update existing hl7log-extractor to poll Kafka instead of file system.

### Apache Camel K

Use **Apache Camel K** for the MLLP listener:

- [Camel K](https://github.com/apache/camel-k) is a Kubernetes operator that deploys Camel routes as custom resources
- Define integration as YAML, operator handles container build and deployment
- Built-in MLLP and Kafka components (production-tested)
- No Dockerfile, no Helm chart needed - just apply the Integration CR

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

### Processing Integration

- Existing `hl7log-extractor` (Temporal workflow) updated to poll Kafka instead of file system
- Extractor batches messages into ZIPs, uploads to object storage (Bronze)
- Existing `hl7-transformer` parses HL7, writes to Delta Lake (Silver)
- Delta Lake, Trino, Superset unchanged

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
| python-hl7 mllp to Kafka | More code to write/maintain, need to publish images and create helm chart  |
| Direct to object storage | Too many small writes, performed poorly, slow ACKs |
| Listener to file system | Too many small writes, slow ACKs |

### Cloud Deployment

Running the HL7 listener in the cloud (e.g., Azure) would provide significant durability benefits:

- **Higher availability** than on-prem K3s cluster which experiences periodic outages
- **Isolated infrastructure** reduces risk of local network issues affecting HL7 ingestion

However, our air-gapped deployment prevents this approach. The on-prem network cannot be opened to external cloud services.

## Architecture

### Component Overview

```
HL7 Source ──► hl7-listener ──► Kafka ──► hl7log-extractor ──► Object Storage (Bronze)
   (MLLP)      (Camel K)     (hl7-messages)   (Temporal)              │
                    │                              │                  │
                    ▼                              ▼                  ▼
                ACK fast                    Batch into ZIPs     hl7-transformer
                                                                      │
                                                                      ▼
                                                                Delta Lake (Silver)
```

| Component | Type | Lifecycle | Purpose |
|-----------|------|-----------|---------|
| hl7-listener | Camel K Integration | Runs continuously | Receive MLLP, write to Kafka, ACK |
| Kafka | Message broker | Runs continuously | Durable buffer, 7-day retention |
| hl7log-extractor | Java/Spring (Temporal) | Polls Kafka | Batch messages into ZIPs, upload to S3 (Bronze) |
| hl7-transformer | Python/Spark (Temporal) | On-demand | Parse HL7, write to Delta Lake (Silver) |

### Changes to Existing Scout Components

| Component | Change Required |
|-----------|-----------------|
| hl7log-extractor | Add Kafka consumer to poll Kafka instead of file system |
| hl7-transformer | Minimal changes - same ZIP input format; write ADT messages to `adt_messages` table |
| Delta Lake schema | Add `adt_messages` table for raw ADT storage; future phases add patient_id_mapping table |

## Database End State

### The Problem

**Report Updates:** Same report arrives multiple times as status changes:

```
08:00  ORU^R01  Report 123  Status: P (Preliminary)
08:30  ORU^R01  Report 123  Status: F (Final)
09:00  ORU^R01  Report 123  Status: C (Corrected)
```

**Patient Merges:** ADT^A40 messages indicate two patient records have been merged:

```
ADT^A40: Patient 78901 merged into Patient 78900
→ Historical reports filed under 78901 are orphaned
→ Query for Patient 78900 misses historical data
```

### Phased Approach

**v1: Accept Duplicate Reports , Ignore ADTs (current state)**

- Ingest all ORU^R01 messages as new rows
- Users may see multiple rows per report (Preliminary, Final, Corrected versions)

**v2: Handle Report Updates**

- Add `reports_latest` view with status hierarchy (Corrected > Final > Preliminary)
- Users query view for current report version, base table for full history

**v3: Handle Patient Merges**

- New table: `patient_id_mapping` populated from ADT^A40 messages
- Maps old MRN → surviving MRN with effective date
- Views and queries join through mapping table for accurate patient-centric results

**Future: Patient Info Table**

- Dedicated table for patient demographics sourced from ADT messages
- Separate from reports table, linked by patient identifiers
- Scope TBD - need to research exact contents of ADT^A08, A31, A04, A01, A28

## Open Questions

- [ ] ADT message contents - what exactly is in A08, A31, A04, A01, A28?
- [ ] Patient info table schema - what fields are needed beyond what ORU PID segment provides?
- [ ] Test environment - when available? how to schedule testing?
- [ ] Alerting - who should receive incident alerts?

## Test Plan

### Goal

Validate end-to-end message flow with test environment before production deployment.

### Steps

1. Deploy Camel K operator to cluster
2. Deploy Kafka to cluster (or connect to existing)
3. Create Kafka topic: `hl7-messages`
4. Apply `hl7-listener` Integration CR
5. Update hl7log-extractor to poll Kafka
6. Connect hl7-listener to test environment
7. Validate message flow:
   - Messages received and ACKs sent correctly
   - No upstream queue backup
   - Messages appear in Kafka
   - Extractor batches messages into ZIPs
   - ZIPs written to object storage
   - Transformer processes ZIPs
   - Data appears correctly in Delta Lake
8. Test failure scenarios:
   - Listener restart - verify no message loss (Kafka durability)
   - Extractor restart - verify Kafka replay works
   - Transformer failure - verify Temporal retry works

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

| Phase | Scope |
|-------|-------|
| **0: Design** | Finalize architecture |
| **1: Build** | Deploy Camel K operator, create hl7-listener Integration, update hl7log-extractor to poll Kafka |
| **2: Test** | Connect to test environment, validate end-to-end flow |
| **3: Production** | Production connection, monitoring, alerting |
| **4: Updates (v2)** | Handle report status updates with `reports_latest` view |
| **5: Merges (v3)** | Patient ID mapping table for ADT^A40 processing |
| **6: Patient Info** | Dedicated patient demographics table from ADT messages |

## References

- [Apache Camel K GitHub](https://github.com/apache/camel-k)
- [Apache Camel MLLP Component](https://camel.apache.org/components/4.8.x/mllp-component.html)
- [Apache Camel Kafka Component](https://camel.apache.org/components/4.8.x/kafka-component.html)
- [hl7-to-kafka](https://github.com/diz-unimr/hl7-to-kafka)
- [python-hl7 MLLP](https://python-hl7.readthedocs.io/en/latest/mllp.html)
- [MLLP Protocol Specification](https://rhapsody.health/resources/mlp-minimum-layer-protocol/)
- [HL7 v2.x Message Types](https://hl7-definition.caristix.com/v2/HL7v2.7/TriggerEvents)
