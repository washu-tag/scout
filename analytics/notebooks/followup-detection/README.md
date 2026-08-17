# Follow-Up Detection

Classifies radiology reports for follow-up recommendations using an LLM, then exposes the results in a Voilà review playbook so clinicians can spot-check and annotate.

The pipeline classifies each report from the curated silver-layer table `default.reports_latest`, persists results into a working table `default.reports_followup`, and routes failures to `default.followup_errors`. The review playbook reads `reports_followup`, presents a stratified sample, and writes reviewer verdicts back into the same table.

## Prerequisites: trino-rw network access

The pipeline creates and writes `default.reports_followup`, and the user-facing Trino
is read-only by design — `delta.security=READ_ONLY`, a readonly metastore and reader S3
credentials (ADR 0019). Writes therefore go to `trino-rw`, which holds the writable
metastore endpoint and lake-writer credentials server-side. Nothing else is needed: no
Spark, no JVM, no JARs, and no S3 secret to enter.

What is needed is network access. `trino-rw` is reachable only from the hl7-transformer,
Voilà and Prometheus by default, so the singleuser pods need **two** policies — traffic
has to satisfy the source's egress *and* the destination's ingress:

```yaml
# jupyter-trino-rw.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: jupyter-trino-rw-egress
  namespace: scout-analytics # jupyter_namespace
spec:
  podSelector:
    matchLabels: # matches the chart's own `singleuser` policy
      app: jupyterhub
      component: singleuser-server
      release: jupyter
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: scout-extractor
          podSelector:
            matchLabels:
              app.kubernetes.io/instance: trino-rw
              app.kubernetes.io/name: trino
      ports:
        - port: 8080
          protocol: TCP
---
# Additive, rather than editing the chart-managed trino-rw policy, so a redeploy
# cannot silently drop it.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: trino-rw-allow-jupyter
  namespace: scout-extractor
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/instance: trino-rw
      app.kubernetes.io/name: trino
      trino.io/network-policy-protection: enabled
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: scout-analytics
          podSelector:
            matchLabels:
              app: jupyterhub
              component: singleuser-server
              release: jupyter
      ports:
        - port: 8080
          protocol: TCP
```

```bash
kubectl apply -f jupyter-trino-rw.yaml
```

Verify from a running notebook before starting a long run:

```python
import socket; s = socket.socket(); s.settimeout(5)
s.connect(("trino-rw.scout-extractor", 8080))  # no exception = reachable
```

**This widens ADR 0019 deliberately, so remove it afterwards.** `trino-rw` is
unauthenticated: any singleuser pod that can reach it can write to the lake. The user
name the notebook connects with is an audit label, not a credential.

```bash
kubectl delete networkpolicy -n scout-analytics jupyter-trino-rw-egress
kubectl delete networkpolicy -n scout-extractor trino-rw-allow-jupyter
```

A longer-term improvement (deferred) would be an inventory flag that renders both
policies conditionally, rather than applying them by hand.

## Contents

| File | Role |
|---|---|
| `followup_detection.ipynb` | Pipeline notebook — creates the working table, classifies reports in batches via Ollama, MERGEs results through `trino-rw`. Run from JupyterHub. |
| `followup_review_dashboard.py` | Voilà / ipywidgets review UI — accept / reject / edit classifier output, save back to the working table. |
| `FollowUpDetection.ipynb` | One-cell Voilà launcher for the review UI. Linked from the Launchpad home page. |

## Running the pipeline

In JupyterHub, open `followup_detection.ipynb` and run cells top to bottom:

1. **Imports + config** — reads `OLLAMA_URL`, `OLLAMA_MODEL`, `TRINO_RW_HOST` etc. from env (Scout-friendly defaults baked in).
2. **Connection** — opens the `trino-rw` connection and defines `q()` / `x()`, which reconnect and retry so a dropped socket does not end a multi-day run.
3. **One-time setup: working table** — `DROP` + `CREATE` `reports_followup` from `reports_latest`. Guarded: it refuses to drop a table that already holds classified rows unless `REALLY_RECREATE = True`, so re-running the notebook to resume cannot destroy days of work.
4. **Top-up** — `NOT EXISTS` inserts new accessions from `reports_latest` without disturbing previously-classified rows. **Run whenever new HL7 ingests have landed.**
5. **Classifier** — defines the JSON-formatted prompt and the Ollama call.
6. **Read/write helpers** — batch fetch, chunked MERGE, error logging. Defined before the test so the test exercises the same write path as the full run.
7. **Test run** — 100 reports, classified **and written back**, then re-queried to confirm the MERGE landed. Prints an extrapolated runtime for the full corpus.
8. **Full pipeline** — sweep over unprocessed rows, parallelised via `ThreadPoolExecutor`. Failures land in `followup_errors`; a report is abandoned after `MAX_ATTEMPTS` so a persistently failing row cannot be retried forever.
9. **Summary** — detection rate, confidence breakdown, error counts, and how many reports were abandoned.

Resuming is safe: rows are selected on `followup_processed_at IS NULL`, so re-running the
pipeline cell picks up where it stopped. Skip the setup cell when resuming.

## Running the review playbook

The Launchpad home page links to `FollowUpDetection.ipynb`, which loads `followup_review_dashboard.create_landing_page(samples_per_category=50)`. From there, click **Launch Dashboard** to load a stratified sample (~50 rows per modality × detection × confidence cell).

Reviewer actions per row: **Accept / Reject / Edit**. **Export annotations** runs an idempotent `ALTER TABLE ADD COLUMNS` (for the `human_*` columns the first time) followed by a `MERGE` into `default.reports_followup`.

## Tables produced

### `default.reports_followup` — working table

A copy of `default.reports_latest` (one row per `accession_number`) plus columns for the LLM classifier output and reviewer annotations. Owned by `followup_detection.ipynb` so concurrent `hl7-transformer` ingests don't fight writes.

**Identifiers** (from `reports_latest`):

- `primary_report_identifier` STRING — unique key, used for MERGE
- `accession_number` STRING — used by the playbook UI ("Jump to accession")

**Classifier input** (from `reports_latest`):

- `report_text` STRING

**Display / filter columns** (from `reports_latest`):

- `modality`, `service_name`, `service_identifier`, `message_dt`
- `patient_age`, `sex`, `race`, `sending_facility`
- `diagnoses` (array of structs), `principal_result_interpreter`

**Classifier output** (written by `followup_detection.ipynb`):

- `followup_detected` BOOLEAN — `NULL` = unprocessed
- `followup_confidence` STRING — `high` or `low`
- `followup_finding` STRING — `<category>: <detail>` (e.g., `Pulmonary nodule: 8 mm right upper lobe`)
- `followup_snippet` STRING — verbatim excerpt with the recommendation
- `followup_processed_at` TIMESTAMP

**Reviewer annotations** (written by `followup_review_dashboard.py` on first export):

- `human_ground_truth` BOOLEAN — `NULL` = not yet reviewed
- `human_notes` STRING
- `human_reviewed_at` TIMESTAMP

### `default.followup_errors` — error log

Created on first failure by `followup_detection.ipynb`. One row per classification call that raised.

- `primary_report_identifier` STRING
- `error` STRING — exception message, truncated to 500 chars
- `error_timestamp` TIMESTAMP

## Configuration

Connection details come from environment variables JupyterHub sets by default:

- **Ollama** (pipeline): `OLLAMA_URL` (default `http://ollama.scout-analytics:11434`), `OLLAMA_MODEL` (default `gemma4-31b-long:latest`)
- **Trino, writes** (pipeline): `TRINO_RW_HOST` (default `trino-rw.scout-extractor`), `TRINO_RW_PORT` (default `8080`). The connecting user defaults to `JUPYTERHUB_USER` — an audit label, since `trino-rw` is unauthenticated.
- **Trino, reads** (dashboard): `TRINO_HOST`, `TRINO_PORT`, `TRINO_SCHEME`, `TRINO_USER`, `TRINO_CATALOG`, `TRINO_SCHEMA`
