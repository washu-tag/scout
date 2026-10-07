# hl7-listener

Deploys the real-time HL7 pipeline described in [ADR 0028](../../../docs/internal/adr/0028-hl7-listener-architecture.md): `hl7-listener` (MLLP → Kafka topic `hl7-messages`), `hl7-batcher` (Kafka → zipped batches in the `hl7-raw` bucket, with each archive's key published to `hl7-batches`), and the Strimzi Kafka cluster between them.

## Re-archiving messages to bronze

Use this when messages are in Kafka but not in `hl7-raw`. For example, a batcher without `breakOnFirstError` skipped batches whose upload failed, or archived objects were lost. It only works while Kafka still holds the messages. Retention is `kafka_retention_ms`, 2 days by default. Kafka deletes whole log segments, so older data can linger a little longer, but don't count on it.

The procedure rewinds the batcher's consumer group to a point in time, and the batcher re-reads and re-archives everything from there.
- **Kafka is unchanged.** Moving a consumer group's position never changes what Kafka stores.
- **Duplicates:** messages that were already archived get archived again, under keys dated by upload time. Anything reading `hl7-raw` has to tolerate duplicates anyway, since delivery is at-least-once.
- **The feed isn't interrupted.** The listener keeps receiving while the batcher is stopped, and messages wait in Kafka.

The commands assume the default namespace (`hl7_listener_namespace`, i.e. `scout-extractor`) and Kafka cluster name (`kafka_cluster_name: kafka`), which put the broker in pod `kafka-kafka-pool-0`. `--to-datetime` uses the Kafka container's clock, which is UTC.

```bash
K="kubectl -n scout-extractor"
KCG="$K exec kafka-kafka-pool-0 -- /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --group hl7-batcher"
```

1. **Find the rewind point.** In Grafana → Explore (Prometheus), run this over the period in question and note the earliest hour with failures. Rewind to an hour or so before it.
   ```
   sum(increase(camel_exchanges_failed_total{integration="hl7-batcher",eventType="context"}[1h])) > 0.5
   ```
2. **Record the current positions** (`CURRENT-OFFSET` per partition):
   ```bash
   $KCG --describe
   ```
3. **Stop the batcher,** and wait until its pod is gone. Kafka won't move the position of a group with connected consumers.
   ```bash
   $K scale deploy/hl7-batcher --replicas=0
   ```
4. **Preview the rewind and the oldest data still available,** e.g. for 12:00 UTC on 5 October 2026:
   ```bash
   $KCG --topic hl7-messages --reset-offsets --to-datetime 2026-10-05T12:00:00.000 --dry-run
   $KCG --topic hl7-messages --reset-offsets --to-earliest --dry-run
   ```
   - **Each new offset must be lower than that partition's `CURRENT-OFFSET` from step 2.** A higher offset would skip messages instead of re-reading them.
   - **If the new offsets equal the earliest ones,** Kafka no longer has data from that time. Use `--to-earliest` to recover what remains.
5. **Apply it:** the same command with `--execute` instead of `--dry-run`.
6. **Start the batcher:**
   ```bash
   $K scale deploy/hl7-batcher --replicas=1
   ```
7. **Verify:**
   - **Re-run `$KCG --describe`.** `LAG` should start at roughly `CURRENT-OFFSET − new offset` per partition and fall back to its usual level as the batcher catches up.
   - **Then re-run the step 1 query.** It should show no batcher failures after the rewind. If it does, those batches were skipped during the catch-up (only possible without `breakOnFirstError`); repeat from just before them.
