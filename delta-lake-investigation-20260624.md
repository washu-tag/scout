# Preprod Delta Lake Ingestion Failure — Investigation

**Date:** 2026-06-24
**Symptom:** Every ingestion workflow fails with
`[DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE]`, raised from
`curatedtable.py:181 → merge_df_into_dt_on_column → DeltaTable.execute()`, regardless
of which data is being ingested.

## TL;DR

Nothing in the lake is *file-level corrupt*. The base `reports` table is healthy
(intact files, unique merge keys). The real problem is a **permanently wedged Spark
Structured Streaming checkpoint** for the `reports → derivative tables` stream.

- The derivative stream's checkpoint (query id `ade5f617-78ab-4d0d-b7b6-411c3524da15`
  — the exact id in the reported error) is stuck on an **uncommitted micro-batch
  (batch 61)** whose Change-Data-Feed (CDF) read range spans **two base-table commits
  that both update the same 10,321 reports** (`reports` versions 63 and 64).
- The curated-table derivation keeps both versions' `update_postimage` rows **without
  deduplicating on its merge key** (`primary_report_identifier` = `source_file`), so
  the Delta MERGE sees 2 source rows per target row → the error.
- Because the batch never commits, Structured Streaming **replays the identical
  poisoned batch on every run** → it fails identically forever, independent of any new
  data. New ingests (base `reports` is now at v121) pile up unprocessed and are never
  even reached.
- It is **self-amplifying**: base merge and derivative processing run in the *same*
  Temporal activity, so each failure triggers a retry that re-runs the base merge first
  (adding yet another duplicate-update version) before failing again.
- **Origin (6-12):** *not* corrupt data and *not* really a Spark error — a Temporal
  **activity cancellation** (likely a derivative timeout) interrupted the derivative
  *after* its base merge, the code killed/restarted the pod, and the retries
  manufactured the duplicate same-row commits. See "What triggered it" below.

## What triggered it (confirmed via Loki + Temporal, 2026-06-12)

The base data was never the problem. A single **transient activity cancellation**
during the derivative phase, amplified by retries, manufactured the duplicate CDC.

Reconstructed from the transformer's Loki logs (activity `93cb90bd-…`, query id
`ade5f617-…`) on 2026-06-12 (all times UTC):

1. **15:01** — the scheduled ingest of 6-11 data merges into base `reports` as **v62**
   (clean INSERT of 10,321 rows); the reports→derivative stream commits **batch 60**
   (~15:03). All good so far.
2. The *same activity* then keeps doing the heavier derivative work (child tables /
   mapping / epic views) for ~65 min.
3. **16:06:42 (attempt 1)** — a `temporalio.exceptions.CancelledError: Cancelled`
   interrupts a py4j call inside the derivative's `awaitTermination`. I.e. **the
   Temporal activity was cancelled mid-derivative** — most consistent with a
   start-to-close / derivative timeout (the derivative had been running ~65 min), or a
   manual workflow cancel. The code catches the resulting `Py4JNetworkError` as a
   generic *"Spark error … Marking pod unhealthy"*, the health file flips, the liveness
   probe fails, and the kubelet **SIGTERMs the pod** (graceful, `exitCode 0` — *not* an
   OOMKill; one of this pod's **26 restarts** clustered around 6-12).
4. **Temporal retries the activity.** Because base merge and derivative are in the
   *same* activity, each retry re-runs the **base merge first**, appending **v63, v64,
   v65, v66** — all UPDATES of the same 10,321 rows — while the derivative keeps getting
   interrupted/restarted.
5. **16:11:31** — once ≥2 same-row base versions sat un-checkpointed, batch 61's CDF
   replay spanned them and threw `DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW` **for
   the first time**. It has recurred on every run since (the checkpoint can't advance).

**Why preprod wedged but prod didn't.** This is an operational/timing failure, not a
code or data difference. The three latent vulnerabilities below exist in prod too —
prod just never tripped the trigger. On preprod a transient cancellation/timeout
interrupted the derivative *after* a base merge (plausibly aggravated by resource
contention from the concurrent full-dataset ingest, on an already-unstable transformer
pod with 26 restarts), and the retries created duplicate same-row CDC. On prod the
ingest completed without a mid-derivative cancellation → no retry → no duplicate
commits → no wedge.

## How the pieces fit (root-cause chain)

1. **Single activity does both writes.** `import_hl7_files_to_deltalake()`
   (`hl7-transformer/.../deltalake.py`) merges the batch into base `reports`
   (line 503) **and then** runs `process_derivative_data()` (line 511) in the *same*
   Temporal activity.

2. **Derivative tables share one CDF stream + one checkpoint.**
   `dataextraction.py:perform_table_operations` opens a single
   `readStream…option("readChangeFeed","true")…table("default.reports")`, writes to
   checkpoint `…/checkpoints/reports`, and inside one `foreachBatch` processes **all four
   derivative tables** (curated, latest, mapping, diagnosis). If any one throws, the
   **whole batch fails and the checkpoint does not advance.**

3. **Only the curated table is vulnerable to duplicate source keys.**
   - `curatedtable.py:curate_silver_table` uses `filter_df_for_update_inserts()` (keeps
     `insert` + `update_postimage`, **no dedup**) and merges on
     `primary_report_identifier` (= renamed `source_file`) **AND `year`**.
   - `latesttable.py` and `diagnosistable.py` instead call
     `dedupe_df_on_accession_number()`, which collapses duplicate source rows via a
     `row_number()` window. They would *not* hit this error.
   - Curated is **first** in the derivative iteration order, so it fails first and
     aborts the batch before the others run.

4. **The poisoned batch.** The streaming checkpoint's last *committed* batch is **60**
   (`commits/60`, 2026-06-12 10:03 CDT). Batch **61** wrote an offset
   (`offsets/61`, frozen at 2026-06-12 11:10 CDT) but **never committed**. Its CDF range
   is `{reservoirVersion:63, index:-1}` → `{reservoirVersion:64, index:0}`, i.e. it reads
   base `reports` **CDC v63 ∪ v64**. Both commits are MERGE-updates of the **same 10,321
   reports** (the `AdtDftOru_7780_20260611.zip` / 6-11 data), committed ~2.5 min apart on
   6-12 (an attempt + its retry).

5. **The collision (measured directly from the CDC parquet):**

   | source | rows kept (`insert`+`update_postimage`) | distinct `source_file` | colliding keys |
   |---|---|---|---|
   | reports CDC **v63 ∪ v64** | 20,642 | 10,321 | **all 10,321 (2× each)** |

   Every one of the 10,321 target curated rows is matched by 2 source rows →
   `DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE`.

6. **Permanent wedge + amplification.** On every subsequent ingest:
   - Structured Streaming recovers the uncommitted batch 61 and replays the **same**
     v63∪v64 range → fails identically. (Hence "independent of what data is ingested".)
   - The activity throws → Temporal retries it → the base merge (step 503) re-runs with
     the same dataframe, appending **another** duplicate-update commit to `reports` →
     derivative fails again. We observe **~5 commits per daily run** (1 attempt + 4
     retries), ~2 min apart, ~24 h between clusters — matching the daily
     `ScheduledReportIngest` schedule. Base `reports` has advanced 60→121 this way while
     the derivative checkpoint has not moved since 6-12.

## Evidence

- **Checkpoint** (`lake/delta/checkpoints/reports/`): `commits/` max = **60**;
  `offsets/` max = **61** (uncommitted), content `reservoirVersion:64,index:0`,
  mtime **2026-06-12 11:10:32 CDT** (frozen). `metadata` query id =
  `ade5f617-…` = the id in the reported error.
- **Base table** (`lake/delta/reports/_delta_log/`): latest version **121**. Commits
  63–121 are all MERGEs; metrics show repeated `numTargetRowsMatchedUpdated` of identical
  row counts in clusters of ~5 (e.g. 10,442 inserted at v67 then re-updated at
  v68–v71). All CDC for this window is in partition `year=2026`. Base merges are 1:1
  (`numSourceRows == numTargetRowsUpdated`) → **base keys are unique; base table is not
  corrupt.**
- **CDC content**: v63 alone = 10,321 *distinct* `source_file` (clean). v63 ∪ v64 =
  10,321 distinct over 20,642 rows (every key duplicated) — the failing batch's source.
- **Sample colliding key**:
  `s3://lake/hl7/2026/AdtDftOru_7780_20260611.zip/2026/06/11/AdtDftOru_7780_20260611_10197.hl7`.

## Impact

- **Base `reports`: current and correct** (through v121).
- **All derivative tables are stale since ~2026-06-12 10:03 CDT** — `reports_curated`,
  `reports_latest`, `reports_dx`, `reports_report_patient_mapping(_history)` — plus the
  `*_epic_view` views built on them. They are missing every report ingested from 6-12
  afternoon onward. (Note: even the *deduping* tables are stale, because curated aborts
  the shared batch before they run.)

## Remediation

### Will the dedup fix clear the outstanding backlog? — Yes.

Deduping the curated source per micro-batch (keep newest by `message_dt` per
`primary_report_identifier`, exactly what `latest`/`diagnosis` already do via
`dedupe_df_on_accession_number`) makes each batch's source unique, so:

- the wedged **batch 61** (v63∪v64) replays, dedupes to 10,321 unique rows, **commits**,
  and the checkpoint finally advances;
- the stream then drains the **v65→v121+ backlog** in subsequent batches (each batch
  deduped the same way);
- `mapping` derives from `reports_curated` (post-dedup) so it's fine; `latest`/`diagnosis`
  already dedup. **Curated dedup is the only code change needed**, and it touches no data.

Caveats: the catch-up batch(es) will be large (~60 base versions of churn). Give the
activity enough time/memory — and ideally fix the trigger too (next section), or a slow
catch-up could itself be cancelled and re-wedge.

### Is there a targeted data fix to recover *without* the dedup fix? — Yes, but less durable.

The base `reports` table is clean and current; only the derivative checkpoint + its
unprocessed CDC window are poisoned. So you can recover from the data side:

- **Recommended data-side option — rebuild the derivative pipeline.** Drop/truncate the
  derivative tables and delete the derivative checkpoints (`…/checkpoints/reports` and
  the child `…/checkpoints/reports_curated`, etc.), rebuild each derivative table from a
  **full batch read** of the current base `reports` table, then let CDF streaming resume
  from the current version. Because the base data is correct, derivatives recompute
  correctly and the poisoned CDC window is bypassed entirely. Downside: rebuild all
  derivative tables + epic views; brief derivative-table unavailability.
- **Not recommended — surgically force the checkpoint past the dup range** (mark batch 61
  committed / hand-edit offsets). Fragile, skips applying those CDC records, easy to get
  wrong.
- Compaction does **not** help — CDF history can't be rewritten to remove the duplicate
  change records.

**Bottom line:** the dedup fix alone resolves the backlog *and* hardens the pipeline, so
a separate data fix isn't required. A data-side rebuild only buys you a faster
"unstuck" without deploying code — but **without the dedup fix the pipeline stays
fragile**: the next cancellation/timeout that interrupts a derivative after a base merge
will re-wedge it the same way.

### Harden the trigger path (recommended regardless)

1. **Decouple base merge from derivative processing** (or make the derivative step
   idempotent / independently retryable) so a derivative failure/cancel doesn't re-merge
   the base table and manufacture duplicate same-row CDC on every Temporal retry. This is
   the amplifier that turned one transient cancel into a permanent wedge.
2. **Handle activity cancellation distinctly from Spark errors.** A
   `temporalio.CancelledError` mid-py4j is currently reported as a "Spark error → mark
   pod unhealthy → restart." Cancellation/timeout shouldn't kill the pod.
3. **Right-size the derivative timeout.** The derivative phase ran ~65 min before being
   cancelled; confirm the start-to-close/`deltaIngestTimeout` covers the realistic
   derivative + mapping + epic-view runtime, especially under concurrent-ingest load.

## The Postgres `file_statuses` error rows — two distinct causes

The ingest-status DB (`ingest` database on `postgresql-cluster`, scout-core) has
**5,665,043 `failed` HL7 rows**. The merge error itself writes nothing (the Python
activity's `except` just re-raises); the rows come from the **Java workflow**
`IngestHl7ToDeltaLakeWorkflowImpl` — on any `ActivityFailure` it calls
`writeHl7FilesErrorStatusToDb`, marking **every HL7 file in that run's manifest**
`failed` with `"Error ingesting HL7 to delta lake: " + e.getMessage()`. So one failed
run produces *manifest-size* rows. Breakdown by `workflow_id`:

| When | workflow_id | rows | `e.getMessage()` | Cause |
|---|---|---|---|---|
| 2026-06-05 | `59e3b220…` | 3,040,278 | **`Activity task timed out` … MAXIMUM_ATTEMPTS_REACHED** | full-dataset backfill **timed out** |
| 2026-06-06 | `04f76828…` | 2,422,329 | **`Activity task timed out` … MAXIMUM_ATTEMPTS_REACHED** | full-dataset backfill **timed out** |
| 2026-06-12 → 24 | daily ids (`a167dec6…`, `f9b8d494…`, … `c5a74e0c…`) + 6-23 cluster | ~202,429 | **`Activity task failed`** (`identity='…hl7-transformer…'`) | the **merge wedge** |
| 6-04/05 | `24db33a4…`, `0b8b9725…` | 7 | "File is not parsable as HL7" / "empty" | unparsable messages |

**Answer to "what caused all those errors": ~96% are a separate, earlier event, not the
wedge.** The two 6-05/6-06 runs are the **full-dataset backfill timing out** — manifests
of ~3.04M and ~2.42M files whose `ingest_hl7_files_to_delta_lake` activity exceeded its
Temporal timeout (retried to max attempts, `identity=''` — the worker never reported
back), so the workflow flagged every file in each manifest. These predate the wedge
(which began 6-12, when the derivative checkpoint was still advancing) and carry a
different failure (`timed out`, not the merge error).

Only the ~202k rows from 6-12 onward (one daily manifest each, sizes matching the base
commits: 10,321 / 10,442 / …, plus the larger 6-23 cluster) are the merge wedge
(`Activity task failed`).

**Common root, different symptom.** Both stem from the same coupled
base-merge + derivative activity exceeding its Temporal timeout on heavy work. On
6-05/6-06 the *full dataset* timed out outright → millions of `failed` rows, no wedge.
On 6-12 a *daily* run's derivative was cancelled/timed-out **after** its base merge
committed → Temporal retry re-merged the same rows → duplicate CDC → the permanent
merge wedge. So the giant error count and the wedge are siblings of the same timeout
problem, but the 5.46M rows are the backfill timeout, not the wedge.

(Counts reconcile exactly: 3,040,278 + 2,422,329 timeout + 202,429 wedge + 7 parse =
5,665,043 = total HL7 `failed`.)

## What was NOT the problem

- No corrupt/unreadable parquet or `_delta_log` entries.
- No duplicate keys *within* the base `reports` table (base MERGEs are 1:1).
- Not specific to any particular report content — purely a function of the stuck
  checkpoint range and the curated table's missing dedup.
