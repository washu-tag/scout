# Data Authorization Integration Tests

End-to-end tests for the RBAC pipeline introduced by ADRs 0020 and 0021:

```
Keycloak admin set user attribute
    ↓ AdminEvent
OPA bundle publisher SPI (in keycloak/event-listener/)
    ↓ tar.gz PUT
MinIO opa-bundles bucket
    ↓ 5-10 s poll
OPA bundle plugin (atomic data.users swap)
    ↓ rego eval against synthetic Trino inputs
Trino /v1/statement (JWT auth, X-Trino-User impersonation)
    ↓ row filter + column mask applied
Result rows
```

The tests prove every link in that chain works for a real query against a
real table. Run automatically in CI's `smoke-test` job (see
[`.github/workflows/ci.yaml`](../../.github/workflows/ci.yaml) — the steps
`Data authorization tests - seed` and `Data authorization tests - run`).
Also runnable manually against any deployed Scout cluster — see
[Running locally](#running-locally) below.

## Architecture

Two Kubernetes Jobs, scheduled in sequence:

1. **`seed/`** — runs once per CI cluster. Uses the `hl7-transformer`
   image (which ships PySpark + Delta + s3a + Hive client) to write
   six synthetic rows to `delta.default.test_reports`. Picks
   `sending_facility` / `modality` / `patient_name` /
   `full_patient_name` / `zip_or_postal_code` so the rego's row filter
   and column-mask paths both have something to operate on.
   * `seed.py` — the PySpark script
   * `job.yaml` — the Kubernetes Job manifest (templated; the
     workflow inlines `seed.py` as a ConfigMap entry and substitutes
     the image tag)

2. **`rbac-tests/`** — runs after the seed completes. Uses
   `alpine/curl` + `jq` + `bash` to walk through 8 scenarios that
   each: set Keycloak user attributes, wait for OPA's `data.users` to
   reflect the change, issue a Trino query as that user, assert on
   the response.
   * `run.sh` — the test scenarios
   * `job.yaml` — the Kubernetes Job manifest (templated; the
     workflow inlines `run.sh` and the admin/service-principal
     secrets at apply time)

## Scenarios

| # | User state | Query | Expected |
|---|---|---|---|
| 1 | `allowed_facilities=["ABCHOSP1"]`, all modalities | `SELECT COUNT(*) FROM test_reports` | 3 rows (only ABCHOSP1) |
| 2 | `allowed_facilities=["*"]`, all modalities | same | 6 rows (full count) |
| 3 | `allowed_facilities=["ABCHOSP1"]`, `allowed_modalities=["CT"]` | same | 2 rows (intersection) |
| 4 | no attributes set | same | 0 rows (`1=0` clamp) |
| 5 | `allowed_facilities=["*"]`, `mask_phi_fields=["true"]` | `SELECT patient_name LIMIT 1` | `'[REDACTED]'` |
| 6 | default attrs (no bypass) | `SELECT * FROM reports_report_patient_mapping` | permission denied |
| 7 | `bypass_view_only_tables=["true"]` | same | not denied at `/allow` (may surface table-not-found in CI, that's fine) |
| 8 | `enabled=false` | `SELECT 1` | denied |

Each scenario polls OPA's `/v1/data/users/<user>` before issuing the
Trino query to confirm the bundle propagated; with a 30 s timeout the
typical case completes in 5-10 s.

## Why a separate suite

`tests/ingest` connects to Trino via Spark direct, bypassing Trino's
OPA enforcement entirely. Adding RBAC tests there would mean adding a
Trino-JDBC path to that suite plus all the JWT plumbing — possible but
mixes two test surfaces. Keeping data authorization tests separate
matches the way the RBAC pipeline is conceptually distinct from data
correctness: ingest tests verify the data shape that lands in the
lake; data-authorization tests verify the query layer correctly
restricts who can see which rows.

## Running locally

Against a live Scout cluster you have kubeconfig access to:

```bash
# Populate the seed table (one-time)
IMAGE=ghcr.io/washu-tag/hl7-transformer:<tag-matching-your-deploy>
SEED_PY=$(sed 's/^/    /' tests/data-authorization/seed/seed.py)
awk -v image="$IMAGE" -v seed_py="$SEED_PY" \
    '{ if (/PLACEHOLDER_SEED_PY/) { print seed_py } \
       else { gsub(/PLACEHOLDER_HL7_TRANSFORMER_IMAGE/, image); print } }' \
    tests/data-authorization/seed/job.yaml | kubectl apply -f -
kubectl wait --for=condition=complete --timeout=300s \
    job/data-authz-seed -n scout-data

# Run the tests (needs your Keycloak admin pw + superset_svc secret
# from inventory)
KC_PW="<from inventory keycloak_bootstrap_admin_password>"
SVC_SECRET="<from inventory keycloak_superset_svc_client_secret>"
RUN_SH=$(sed 's/^/    /' tests/data-authorization/rbac-tests/run.sh)
awk -v run_sh="$RUN_SH" -v kc_pw="$KC_PW" -v svc_secret="$SVC_SECRET" \
    '{ if (/PLACEHOLDER_RUN_SH/) { print run_sh } \
       else { gsub(/PLACEHOLDER_KC_ADMIN_PASSWORD/, kc_pw); \
              gsub(/PLACEHOLDER_SUPERSET_SVC_CLIENT_SECRET/, svc_secret); \
              print } }' \
    tests/data-authorization/rbac-tests/job.yaml | kubectl apply -f -
kubectl logs -f -n scout-analytics job/data-authz-tests
```

Re-running just `data-authz-tests` against an already-seeded cluster
is the inner loop while iterating on `run.sh` — the seed Job's table
write is `mode("overwrite")` and idempotent, so re-applying the seed
is also safe.

## CI inventory differences from production

`.github/ci_resources/inventory.yaml` overrides three RBAC-related
groups so the assertions work without running HL7 ingest:

* `trino_filtered_tables`: `test_reports` only (production lists all
  the `reports*` tables; the test fixture has only this one).
* `trino_attribute_filters`: `allowed_facilities` + `allowed_modalities`
  (matches the default but explicit here so the multi-dimension
  scenarios are exercised).
* `trino_view_only_tables`: `reports_report_patient_mapping` (the
  table itself isn't created in CI; the deny rule still fires on a
  non-existent table, which is the correct safety posture).

If you add a new RBAC dimension or move attributes around, mirror the
change in the CI inventory.
