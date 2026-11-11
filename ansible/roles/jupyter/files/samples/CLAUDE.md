# Scout notebook agent guide

You are working in a user's Scout notebook server. Scout holds HL7 radiology reports in
a Delta Lake, queried through Trino with the bundled `scout` Python SDK. A typical
request is a longitudinal question about patients over time, answered with a notebook
that saves its result as a CSV.

`~/Scout` is a read-only folder of samples. Read `~/Scout/Quickstart.ipynb` for the
basics and `~/Scout/Advanced.ipynb` for worked longitudinal queries, then write your own
files elsewhere.

## Workflow

1. Create `~/analyses/<short-slug>/` and write a new notebook there, e.g.
   `~/analyses/chest-ct-followup/chest-ct-followup.ipynb`. Start it from the
   Quickstart's first cells: `import scout`, then a `FACILITIES` list.
2. Settle `FACILITIES`, the facilities this question's IRB covers. Every query filters
   on `contains(:facilities, sending_facility)`.
   - If the user named facilities, use those.
   - Otherwise, list the facilities the user can access. Trino's row filter already
     limits this to their access, so run it once per analysis:
     `SELECT sending_facility, COUNT(*) AS reports FROM reports_latest GROUP BY sending_facility ORDER BY reports DESC`
   - If it returns one facility, use it. If it returns several, show them and ask the
     user which ones the IRB covers, offering "all of these" as an option. Access is
     not IRB approval, so never choose for them.
   - Record the facilities and how they were chosen in the definitions cell.
3. Explore before writing the final query: run small aggregate queries (counts,
   `DISTINCT modality`, `SHOW COLUMNS FROM reports_latest_epic_view`) to confirm column
   names and values instead of assuming them.
4. Put the final query in its own cell, with a markdown cell above it stating the
   question and the definitions you chose (index exam, time window, inclusion rules).
5. Save the result with `df.to_csv(<path>, index=False)` to
   `~/analyses/<short-slug>/<short-slug>.csv`.
6. Check the final query on a single `year` first, then execute the whole notebook so
   it is reproducible:
   `jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=1800 <notebook>`
   If a full run times out, narrow the query or stream it with `scout.connect()`
   rather than raising the timeout without limit.
7. Report back the CSV path, row count, distinct patient count, the column list, and
   the definitions you chose. The user downloads the CSV from the file browser.

## Querying

```python
import scout
df = scout.query("SELECT ... WHERE contains(:facilities, sending_facility)",
                 params={"facilities": FACILITIES})
```

- `scout.query()` returns a pandas DataFrame and runs as the user, so their row filters
  and column masks apply. Results you cannot see are filtered by policy, not missing.
- For results too large for memory, use `scout.connect()` and `fetchmany()`, as in
  `~/Scout/Advanced.ipynb`.
- Match a column against a list with `contains(:vals, col)`. `IN (:vals)` does not work:
  the SDK does not expand list parameters.
- Filter array-of-struct columns with `any_match()`, e.g.
  `any_match(diagnoses, d -> d.diagnosis_code LIKE 'I63%')`.
- Search report text with `regexp_like(col, '(?i)pattern')`, preferring
  `report_section_impression` over the full `report_text`.

## Longitudinal queries

- Query `reports_latest_epic_view` (one row per report, latest version). Use
  `reports_latest` only for report-level questions that never group by patient.
- Take every count in one analysis from the same view. The `_epic_view` tables leave
  out reports whose patient links are inconsistent, so their counts do not match
  `reports_latest`.
- A patient is `scout_patient_id`. It links reports across the patient-ID changes in
  the source data. Group, partition, and join on it; never on `epic_mrn`, `mpi`, or the
  `patient_ids` array. `resolved_epic_mrn` is for display and export only.
- Order a patient's exams by `requested_dt`. Use `observation_dt` when the question is
  about when the exam happened and it is populated.
- Use window functions for sequences: `ROW_NUMBER()` for the first or index exam,
  `LAG()`/`LEAD()` for prior and next exams, all
  `OVER (PARTITION BY scout_patient_id ORDER BY requested_dt)`.
- Use `date_diff('day', a, b)` (or `'month'`, `'year'`) for intervals.
- `year` is the partition column, but it comes from `message_dt` (when the message
  was sent), not the exam date. Use it to narrow the scan, with a margin, and apply
  the real date rule on `requested_dt`.
- Patterns in `~/Scout/Advanced.ipynb`: patients with exams spanning N years (group by
  patient, `HAVING`), N exams within a rolling window (`LEAD`), and imaging before an
  index event (CTE for the index date, join back).

## Output CSV

- One row per report unless the question asks for one row per patient.
- Always include `scout_patient_id`. Include `accession_number` and `requested_dt` for
  report-level rows, and `resolved_epic_mrn` so the user can match patients elsewhere.
- Add the question-specific columns (index date, interval, counts, flags) with
  descriptive snake_case names.
- Do not include `report_text` unless the user asks for it.

## Protected health information

Everything you read, including query output, is sent to the model provider. Keep
patient-level data out of the conversation:

- Inspect results with `df.shape`, `df.dtypes`, `df.isna().sum()`, and counts or
  `value_counts()` on non-identifying columns such as `modality` or year.
- Do not print rows, `df.head()`, MRNs, accession numbers, names, dates of birth, or
  report text, and do not read the CSV back after writing it.
- Executed notebooks store cell output in the `.ipynb` file, and you read that file when
  you edit it. Never end a cell with a DataFrame or a query result; end it with
  `df.shape`, a count, or nothing. Write `df.to_csv(...)` as a statement, not the last
  expression of a display cell.
- Leave row-level inspection to the user in the notebook.
