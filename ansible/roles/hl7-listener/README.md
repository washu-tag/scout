# hl7-listener

Deploys the real-time HL7 pipeline described in [ADR 0028](../../../docs/internal/adr/0028-hl7-listener-architecture.md): `hl7-listener` (MLLP → Kafka topic `hl7-messages`), `hl7-batcher` (Kafka → zipped batches in the `hl7-raw` bucket, with each archive's key published to `hl7-batches`), and the Strimzi Kafka cluster between them.

The commands below assume the default namespace (`hl7_listener_namespace`, i.e. `scout-extractor`) and Kafka cluster name (`kafka_cluster_name: kafka`), which put the broker in pod `kafka-kafka-pool-0`:

```bash
K="kubectl -n scout-extractor"
KCG="$K exec kafka-kafka-pool-0 -- /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --group hl7-batcher"
```

## When the HL7 alerts fire

### HL7 batcher exchange failures

The batcher can't archive a batch to bronze, almost always because object storage is slow, unreachable or refusing the upload.
- **It retries the same batch until the upload succeeds,** pausing 60 seconds between attempts, so nothing is skipped.
- **Consumer lag grows** while it waits, and Kafka holds the messages.
- **The listener and the incoming feed are unaffected.**

1. **See what the uploads fail with:**
   ```bash
   $K logs deploy/hl7-batcher --since=30m | grep -oE '[A-Za-z0-9_.]+Exception' | sort | uniq -c | sort -rn | head
   ```
   - **Timeouts** (`SocketTimeoutException`, `SdkClientException`): object storage is slow or unreachable. Check its pods, and disk latency on the nodes it runs on (Grafana's node dashboards).
   - **`S3Exception`:** the storage answered with an error. The log line has the status: 403 means credentials or a policy change, and 404 means a missing bucket.
2. **Check how far behind it is:** `$KCG --describe` (`LAG` per partition).
3. **Once the cause is fixed,** the batcher catches up by itself and `LAG` falls back to normal. The alert clears 30 minutes after the last failure.

**If the outage could outlast Kafka's retention** (`kafka_retention_ms`, 2 days by default), extend it on the topic before the oldest messages are deleted. Kafka also deletes a partition's oldest data once it exceeds `retention.bytes` (`kafka_retention_bytes`, 2 GiB by default), so raise that too if the backlog is large, after checking the broker's disk:
```bash
$K exec kafka-kafka-pool-0 -- sh -c 'du -sh /var/lib/kafka/data*/kafka-log*/hl7-messages-*; df -h /var/lib/kafka/data*'
$K patch kafkatopic hl7-messages --type merge -p '{"spec":{"config":{"retention.ms":604800000}}}'   # e.g. 7 days
```
Re-running `make install-hl7-listener` puts the configured retention back.

### HL7 listener exchange failures

The listener can't write incoming messages to Kafka, so it NAKs each one. The sending system keeps them queued and resends, so the feed is delayed, not lost.

1. **Check the broker:** `$K get pods | grep kafka`, and the broker's disk (as above).
2. **See what the listener fails with:** the same log command, against `deploy/hl7-listener`.
3. **Once Kafka is back,** the listener reconnects by itself and the sender's queue drains. The alert clears 15 minutes after the last failure. If the sender stopped retrying, ask its interface team to resend.

### HL7 batcher stalled (Kafka lag building)

The listener is receiving messages, but the batcher hasn't handled any batch, failed or not, for 30 minutes. It isn't running, or isn't connected to Kafka.

1. **Check its pod:** `$K get pods | grep hl7-batcher` (status, restarts) and `$K logs deploy/hl7-batcher --since=30m`.
2. **See whether it's in the consumer group:** `$KCG --describe` says "has no active members" when it isn't.
3. **Restart it if it's hung:** `$K rollout restart deploy/hl7-batcher`. It resumes from its last committed offset, so nothing is lost while Kafka still holds the messages.

## Re-archiving messages to bronze

Use this when messages are in Kafka but not in `hl7-raw`. For example, a batcher without `breakOnFirstError` skipped batches whose upload failed, or archived objects were lost. It only works while Kafka still holds the messages. Retention is `kafka_retention_ms`, 2 days by default. Kafka deletes whole log segments, so older data can linger a little longer, but don't count on it.

The procedure rewinds the batcher's consumer group to a point in time, and the batcher re-reads and re-archives everything from there.
- **Kafka is unchanged.** Moving a consumer group's position never changes what Kafka stores.
- **Duplicates:** messages that were already archived get archived again, under keys dated by upload time. Anything reading `hl7-raw` has to tolerate duplicates anyway, since delivery is at-least-once.
- **The feed isn't interrupted.** The listener keeps receiving while the batcher is stopped, and messages wait in Kafka.

`--to-datetime` uses the Kafka container's clock, which is UTC.

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
