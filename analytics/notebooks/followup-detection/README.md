# Follow-Up Detection

Classifies radiology reports for follow-up recommendations using an LLM, then exposes the results in a Voilà review playbook so clinicians can spot-check and annotate.

The pipeline classifies each report from the curated silver-layer table `default.reports_latest`, persists results into a working table `default.reports_followup`, and routes failures to `default.followup_errors`. The review playbook reads `reports_followup`, presents a stratified sample, and writes reviewer verdicts back into the same table.

## Prerequisites: trino-rw NetworkPolicy access

The pipeline notebook creates and writes to `default.reports_followup` via `trino-rw` (the write-enabled Trino instance in `scout-extractor`, ADR 0019). `trino-rw` is locked down by NetworkPolicy to the `hl7-transformer` pod only, so JupyterHub singleuser pods need an explicit allow added. NetworkPolicies are additive, so the drop-in YAMLs below layer on top of the Helm-rendered policies without modifying any role.

Apply both — one extends `trino-rw`'s ingress, one allows Jupyter's egress (both ends need to permit the traffic):

```yaml
# trino-rw-jupyter-access.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: trino-rw-jupyter-access
  namespace: scout-extractor       # trino_rw_namespace
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: trino
      app.kubernetes.io/instance: trino-rw
      app.kubernetes.io/component: coordinator
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: scout-analytics   # jupyter_namespace
          podSelector:
            matchLabels:
              app: jupyterhub
              component: singleuser-server
      ports:
        - port: 8080
          protocol: TCP
---
# jupyter-trino-rw-egress.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: jupyter-trino-rw-egress
  namespace: scout-analytics       # jupyter_namespace
spec:
  podSelector:
    matchLabels:
      app: jupyterhub
      component: singleuser-server
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: scout-extractor   # trino_rw_namespace
          podSelector:
            matchLabels:
              app.kubernetes.io/name: trino
              app.kubernetes.io/instance: trino-rw
      ports:
        - port: 8080
          protocol: TCP
```

```bash
kubectl apply -f trino-rw-jupyter-access.yaml -f jupyter-trino-rw-egress.yaml
```

To revert: `kubectl delete networkpolicy trino-rw-jupyter-access -n scout-extractor; kubectl delete networkpolicy jupyter-trino-rw-egress -n scout-analytics`.

Same shape as the writable-Hive egress policy admins applied for the previous Spark-based pipeline. A longer-term improvement (deferred) would be a `jupyter_allow_trino_rw` inventory flag that adds this conditionally to the Helm-rendered policy.

## Contents

| File | Role |
|---|---|
| `followup_detection.ipynb` | Pipeline notebook — creates the working table, classifies reports in batches via Ollama, MERGEs results. Run from JupyterHub. |
| `followup_review_dashboard.py` | Voilà / ipywidgets review UI — accept / reject / edit classifier output, save back to the working table. |
| `FollowUpDetection.ipynb` | One-cell Voilà launcher for the review UI. Linked from the Launchpad home page. |

## Running the pipeline

In JupyterHub, open `followup_detection.ipynb` and run cells top to bottom:

1. **Imports + config** — reads `OLLAMA_URL`, `OLLAMA_MODEL`, etc. from env (Scout-friendly defaults baked in).
2. **One-time setup: working table** — `DROP` + `CREATE` the `reports_followup` table from `reports_latest`. **Only run on a fresh deployment** (the cell re-drops on every run).
3. **Top-up** — `INSERT … WHERE NOT EXISTS` of new accessions from `reports_latest` into `reports_followup` without disturbing previously-classified rows. **Run whenever new HL7 ingests have landed.**
4. **Classifier** — defines the JSON-formatted prompt and the Ollama call.
5. **Test run** — small batch (~20 reports). Sanity-check the model and prompt before a full sweep.
6. **Full pipeline** — full sweep over unprocessed rows, parallelized via `ThreadPoolExecutor`. Failures land in `followup_errors`.
7. **Summary** — detection rate by modality, confidence breakdown, error counts.

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

Connection details come from environment variables:

- **Ollama** (pipeline): `OLLAMA_URL` (default `http://ollama.scout-analytics:11434`), `OLLAMA_MODEL` (default `gemma4-31b-long:latest`).
- **trino-rw** (pipeline writes): `TRINO_RW_HOST` (default `trino-rw.scout-extractor`), `TRINO_RW_PORT` (default `8080`). The pipeline connects anonymously (gated by NetworkPolicy) and uses `JUPYTERHUB_USER` as the `user` for audit traceability.
- **trino-analytics** (dashboard reads): set by the Voilà role via `scout_trino.connect()` — `TRINO_HOST`, `TRINO_PORT` (HTTPS, 8443), `TRINO_SCHEME=https`, `TRINO_CATALOG`, `TRINO_SCHEMA`, `TRINO_CA_CERT`, plus `KEYCLOAK_TOKEN_URL` and `KEYCLOAK_VOILA_SVC_CLIENT_ID` for the per-user `X-Trino-User` impersonation (ADR 0020). The dashboard pulls the user's OIDC identity from oauth2-proxy's `X-Auth-Request-Access-Token` header; no user-set env vars are required.
