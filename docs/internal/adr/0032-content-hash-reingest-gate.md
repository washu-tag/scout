# ADR 0032: Content-Hash Re-Ingest Gate for the Base Report Merge

**Date:** 2026-07
**Status:** Accepted
**Decision Owner:** TAG Team

## Context

Re-ingesting an HL7 file already in the lake with unchanged content used to rewrite
the `reports` row anyway: every ingest bumped the table version, refreshed `updated`,
emitted per-row change data feed (CDF) events, and drove the full derivative cascade
(`_curated`, `_latest`, `_dx`, mapping) over data that hadn't changed. Because Scout
re-splits and re-processes overlapping log ranges, this churn was routine, not
exceptional — wasteful commits, misleading `updated` timestamps, and derivative-table
work with no result change. See issue #482.

## Decision

Gate the base merge on a per-row **`content_hash`** so an unchanged re-ingest is a
true no-op.

- **Hash.** `with_content_hash` adds `content_hash` = SHA-256 over the JSON
  serialization of every extracted column except the bookkeeping ones (`updated`,
  `content_hash`). Column names are sorted (projection-order-independent) and
  `to_json` options are pinned (`ignoreNullFields`, UTC, fixed timestamp/date formats)
  so the hash is invariant to session timezone, Spark defaults, and the
  per-batch appearance/disappearance of the dynamically pivoted patient-ID columns.
  MapType columns are never hashed (JSON key order is unspecified).
- **Pre-merge anti-join.** Incoming rows are dropped where
  `(source_file, year, content_hash)` already exists. `source_file`/`year` use plain
  `=` (mirroring the MERGE condition exactly, so NULL-`year` rows fall through to
  insert as before); the hash uses null-safe `<=>` so legacy NULL-hash rows still
  backfill once. The `existing` scan is pruned to the batch's year partitions rather
  than reading all of history each ingest. If nothing survives the anti-join, **no
  merge commit is issued** — no CDF, no derivative churn.
- **Conditional matched update** (`NOT (t.content_hash <=> s.content_hash)`) as a
  race/retry safety belt: even if an identical row slips past the anti-join (concurrent
  merge, Temporal activity retry), the matched clause updates nothing and emits no CDF —
  which also makes the ingest activity idempotent under retries.
- **Containment.** `content_hash` lives on the base `reports` table only; the shared
  CDF entry filter (`filter_df_for_update_inserts`) drops it so it never evolves the
  derivative schemas under production's session-wide `autoMerge`.
- **Determinism dependency.** The gate only holds if identical input parses to
  identical rows. A latent bug broke this: OBX lines were assembled with
  `collect_list` (shuffle-order-nondeterministic), so multi-OBX reports hashed
  differently run to run. Extraction now aggregates position-sorted structs and picks
  order-stable values (`min`/first-of-sorted instead of `F.first`), so `report_text`,
  the parsed sections, and `report_status` are file-ordered and stable.

`updated` semantics narrow from "last re-process" to "last content change".

## Consequences

- **Migration is automatic and additive.** Pre-existing tables get a metadata-only
  `ALTER TABLE ADD COLUMNS (content_hash STRING)` on the first post-deploy ingest;
  legacy NULL-hash rows backfill exactly once (null-safe compare), then skip. No
  derivative schema changes.
- **Cost.** Each ingest adds a partition-pruned scan of three base columns; mixed
  batches merge only new/changed rows. An identical 9,988-file re-ingest verified on
  dev03 dropped from a full rewrite + CDF batch + full cascade to a zero-commit no-op.
- **Mixed transformer versions:** avoid running old and new images concurrently — an
  old-image merge NULLs `content_hash` on rows it updates (degraded, re-backfills
  later — not broken).
- **Not addressed here:** the `hl7_files_pkey` duplicate-key failure when re-splitting
  previously-ingested logs (separate ticket).

## Alternatives Considered

| Option | Verdict |
|--------|---------|
| **Content-hash anti-join + conditional matched update (selected)** | Zero-commit no-op on unchanged re-ingest, quiets the whole derivative cascade, idempotent under retries |
| Conditional matched update only (no anti-join) | Rejected: still commits + emits CDF for the insert branch and drives the cascade; the no-op guarantee needs the pre-merge drop |
| Pre-filter entire HL7 files on content hash before spark transformer workflow | Rejected: We would need to track transformer versions in the delta table to dermine if an unchanged file needed to be reingested. |
