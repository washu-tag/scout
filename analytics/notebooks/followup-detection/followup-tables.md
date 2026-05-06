# Follow-up Detection Tables

Two Delta Lake tables back the follow-up detection workflow.

## `default.reports_followup` — working table

A copy of the curated `default.reports_latest` (one row per `accession_number`) plus columns for the LLM classifier output and the human reviewer's annotations. Owned by the follow-up detection notebook so concurrent hl7-transformer ingests don't fight writes.

### Schema

**Identifiers** (copied from `reports_latest`):
- `primary_report_identifier` STRING — unique key, used for MERGE
- `accession_number` STRING — used by the playbook UI ("Jump to accession")

**Classifier inputs** (copied from `reports_latest`):
- `report_text` STRING

**Display / filter columns used by the playbook** (copied from `reports_latest`):
- `modality`, `service_name`, `service_identifier`, `message_dt`
- `patient_age`, `sex`, `race`, `sending_facility`
- `diagnoses` (array<struct>), `principal_result_interpreter`

**Classifier outputs** (populated by `followup_detection.ipynb`):
- `followup_detected` BOOLEAN — NULL = unprocessed
- `followup_confidence` STRING — `high` or `low`
- `followup_finding` STRING — `<category>: <detail>` (closed list of categories; see notebook prompt)
- `followup_snippet` STRING — verbatim excerpt with the recommendation
- `followup_processed_at` TIMESTAMP

**Reviewer annotations** (added by the playbook the first time you export annotations — the dashboard runs an idempotent `ALTER TABLE ADD COLUMNS` before its MERGE; subsequent exports skip the add):
- `human_ground_truth` BOOLEAN
- `human_notes` STRING
- `human_reviewed_at` TIMESTAMP

### Creation

Created automatically by `followup_detection.ipynb` (the "One-time setup: working table" cell). Re-runs of that cell drop and recreate the table; the "Top up" cell ANTI JOINs new accessions in from `reports_latest` without disturbing already-classified rows.

## `default.followup_errors` — error log

Created on first failure by `followup_detection.ipynb`. Each row is a classification call that raised.

### Schema

- `primary_report_identifier` STRING
- `error` STRING — exception message, truncated to 500 chars
- `error_timestamp` TIMESTAMP
