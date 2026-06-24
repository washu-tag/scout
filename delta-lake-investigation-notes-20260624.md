# Investigation notes — preprod delta lake MERGE failure (2026-06-24)

Working notes, useful commands, and interesting/non-obvious findings gathered while
investigating the `DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE` failures.
See `delta-lake-investigation-20260624.md` for the conclusions.

## Access used

- MinIO read-only via `mc` alias `preprod-agent` (buckets `lake`, `scratch`).
  Confirmed working against `localhost:9000`.
- Did **not** end up needing kubeconfig / Loki — the Delta transaction log + CDC parquet
  in MinIO were fully conclusive on their own. The streaming **query id in the
  checkpoint `metadata` file matched the query id in the user's error message**, which
  pinned the failure to one specific checkpoint without any cluster access.

## Key non-obvious findings

1. **The error is about the *source* side, not corrupt files.** Delta's
   `MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW` fires when ≥2 *source* rows match one
   *target* row. Duplicate *target* rows do **not** trigger it (a single source row may
   update many targets). So the hunt was for duplicate source keys, which led to the CDC
   feed — not to the table files.

2. **CDF + a wedged checkpoint manufactures the duplicates.** A single base commit's CDC
   is clean (v63 alone = 10,321 distinct `source_file`). The duplication only appears
   because the **uncommitted** batch's read range spans **two** base commits (v63 ∪ v64)
   that update the *same* rows. The "corruption" is a property of the **checkpoint range**,
   not of any stored data.

3. **Structured Streaming replays an uncommitted batch verbatim.** `offsets/61` exists,
   `commits/61` does not. On every restart Spark re-runs batch 61 over the *same* frozen
   offset range (`rv63 → rv64`, mtime stuck at 2026-06-12 11:10 CDT). That is exactly why
   the failure is independent of newly ingested data, and why base `reports` keeps
   advancing (v60→v121) while no derivative table has been written since 6-12.

4. **Same-activity coupling is the amplifier.** `import_hl7_files_to_deltalake` does the
   base merge (deltalake.py:503) and `process_derivative_data` (deltalake.py:511) in one
   Temporal activity. A derivative failure fails the whole activity → Temporal retries →
   base merge re-runs and writes *another* identical-update version before failing again.
   Visible as clusters of ~5 base commits ~2 min apart, ~24 h between clusters.

5. **Curated is uniquely exposed.** `latesttable`/`diagnosistable` call
   `dedupe_df_on_accession_number` (row_number window) and would survive duplicate
   source rows; `curatedtable` only calls `filter_df_for_update_inserts` (no dedup) and
   merges on `source_file` directly. Curated is also first in the derivative dict, so it
   aborts the shared `foreachBatch` batch before the deduping tables run — which is why
   *all* derivative tables are stale, not just curated.

6. **Offset sentinel semantics.** Delta source offset `index:-1` = "start of version /
   before any data file"; the batch from `{rv63,index:-1}` to `{rv64,index:0}` therefore
   covers v63's CDC plus v64's first (only) change file. Each base commit here produced
   exactly one CDC file under `_change_data/year=2026/`.

## Useful commands / recipes

Read a Delta commit's operation + metrics:
```bash
mc cp -q preprod-agent/lake/delta/reports/_delta_log/00000000000000000063.json /tmp/63.json
python3 -c "import json;[print(l['commitInfo']['operationMetrics']) for l in (json.loads(x) for x in open('/tmp/63.json')) if 'commitInfo' in l]"
```

Find the wedged batch (offset present, commit absent):
```bash
mc ls preprod-agent/lake/delta/checkpoints/reports/commits/ | awk '{print $NF}' | sort -n | tail -1   # 60
mc ls preprod-agent/lake/delta/checkpoints/reports/offsets/ | awk '{print $NF}' | sort -n | tail -1   # 61
mc cat preprod-agent/lake/delta/checkpoints/reports/offsets/61 | tail -1   # reservoirVersion range
mc cat preprod-agent/lake/delta/checkpoints/reports/metadata               # query id -> match the error
```

Inspect CDC parquet for duplicate source keys (duckdb via uv):
```bash
mc cp -q "preprod-agent/lake/delta/reports/_change_data/year=2026/<cdc-file>.parquet" /tmp/v63.parquet
uv run --with duckdb python3 -c "
import duckdb
con=duckdb.connect()
con.execute(\"create view src as select source_file,_change_type from read_parquet(['/tmp/v63.parquet','/tmp/v64.parquet']) where _change_type in ('insert','update_postimage')\")
print(con.execute('select count(*), count(distinct source_file) from src').fetchone())
"
```

Notes on the data:
- All affected CDC is in partition `year=2026`; CDC files ~18 MiB each.
- The cross-version same-row updates are **Temporal activity RETRIES of one run**, not
  cross-day overlap. (Corrected after operator confirmed the normal cadence is "ingest
  day N on day N+1," distinct data each day — under normal operation each derivative
  batch = one base commit of distinct `source_file`s and never collides.) E.g. on 6-12:
  v62 = the normal scheduled INSERT of 6-11 data; v63–v66 = retries of that same run
  (UPDATEs of the identical 10,321 rows) after the run was cancelled mid-derivative.
- Base `reports` MERGE metrics are 1:1 (`numSourceRows == numTargetRowsUpdated`),
  confirming unique base keys.

## Root-cause confirmation (Temporal + Loki, added after follow-up)

The trigger is recoverable from Loki even though Temporal visibility retention had
already rolled off 6-12 (only ~9 workflows remained, oldest 6-22). Loki *did* retain
6-12. Findings:

- The failing transform is `IngestHl7ToDeltaLakeWorkflow`, a child of the scheduled
  `ScheduledReportIngest-<date>` (`IngestHl7LogWorkflow`). Today's run still fails with
  the same query id `ade5f617-…`.
- **First failure was a cancellation, not the dup error.** Transformer logs at
  **2026-06-12 16:06:42 UTC** (activity `93cb90bd-…`, attempt 1):
  `py4j … Error while receiving` → `temporalio.exceptions.CancelledError: Cancelled` →
  `Py4JNetworkError: Error while sending or receiving` →
  `ERROR - Spark error ingesting HL7 files to Delta Lake. Marking pod unhealthy.` The
  py4j break is *caused by* the CancelledError interrupting an in-flight Spark call
  inside the derivative `awaitTermination` (dataextraction.py:119). So the activity was
  **cancelled mid-derivative** (the derivative had been running ~65 min since the 15:01
  base merge → most consistent with a start-to-close/derivative timeout; a manual
  workflow cancel is also possible).
- **Pod restart was graceful, not OOM.** `lastState.terminated = {reason: "Completed",
  exitCode: 0, finishedAt: 2026-06-12T16:08:22Z}`; container limits are generous
  (cpu 8 / mem 128Gi). No OOM/heap lines in logs. The pod died because the app's
  "mark unhealthy" path flipped the health file → liveness probe failed → kubelet
  SIGTERM (seen at 16:07:58). This pod shows **26 restarts, last ~12 d ago** (≈6-12).
- **Retries manufactured the duplicates.** Subsequent attempts log
  `Creating Spark session (attempt 2/3/…)` → `Writing data to Delta Lake table reports`
  (= base merge → v63, v64, v65, …) → `Processing batch (61) on derivative table
  reports_curated` → at **16:11:31** the first
  `DeltaUnsupportedOperationException: DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW`.
- **Code smell:** `deltalake.py` treats *any* `Py4JError`/`ConnectionError` (including a
  cancellation-induced one) as a fatal "Spark error → mark pod unhealthy → restart."
  Cancellation/timeout should not kill the pod, and base-merge+derivative shouldn't share
  one retried activity.

### Useful cluster commands

```bash
export KUBECONFIG=<repo>/.kube/scout/tagpreprod-control-01/config
# Temporal (admintools in scout-extractor; visibility on Elasticsearch — range queries
# errored, so list + filter client-side):
kubectl exec -n scout-extractor deployment/temporal-admintools -- \
  temporal workflow list --namespace default --limit 300 --output json
kubectl exec -n scout-extractor deployment/temporal-admintools -- \
  temporal workflow show --namespace default -w <id> --output json   # base64 input in payloads

# Loki (gateway in scout-monitoring; needs X-Scope-OrgID header):
kubectl port-forward -n scout-monitoring svc/loki-gateway 8080:80 &
curl -sG http://localhost:8080/loki/api/v1/query_range -H 'X-Scope-OrgID: fake' \
  --data-urlencode '{namespace="scout-extractor", pod=~"hl7-transformer.*"} != "[Stage"' \
  --data-urlencode 'start=<ns>' --data-urlencode 'end=<ns>' --data-urlencode 'direction=forward'
# Tip: filter out "[Stage" progress-bar spam; Spark prints thousands of those lines.
```
Retention seen: Temporal visibility ≈ days (rolled off 6-12); Loki retained 6-12 fine.
