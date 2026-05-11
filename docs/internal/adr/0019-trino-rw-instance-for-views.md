# ADR 0019: Read-Write Trino Instance for Transformer-Issued View DDL

**Date**: 2026-05-07
**Status**: Accepted
**Decision Owner**: TAG Team

## Context

The HL7 transformer needs to make joined views of derivative tables (curated, latest, dx) with the report-patient mapping table available to **every Trino consumer in Scout**. The mapping table contains `scout_patient_id`, `resolved_epic_mrn`, and `resolved_mpi` columns that materialize the result of transitive patient-ID resolution; users want to query these alongside derivative tables without having to write the join themselves every time.

Trino is the shared query layer for several Scout services — not just one user-facing tool. Consumers in production today:

- **Superset**: SQL Lab text editor, virtual datasets, charts and dashboards
- **Voilà / Playbooks**: notebooks served as web apps, querying Trino directly
- **Open WebUI / MCP Trino tool**: natural-language SQL via chat (when the optional chat feature is enabled)
- **JupyterHub**: notebooks that go through the Trino Python client (`trino.dbapi.connect`) rather than PySpark

Any solution to the joined-view problem has to serve all of them — not just one. A view that exists only for a subset of these consumers ends up forcing the others to either re-implement the join in client code or remain unable to query the resolved patient IDs at all.

The natural way to expose a "join + filter" via SQL is a view. The transformer's natural place to create one is `spark.sql("CREATE OR REPLACE VIEW ...")` from inside the ingest activity. That works for PySpark consumers in JupyterHub, but **Trino cannot read Spark-created views**: Spark writes a `VIRTUAL_VIEW` row to the Hive Metastore using Spark's own format, while Trino expects either a Trino-native view (`/* Presto View */` payload + base64-encoded JSON) or a Hive-format view that translates cleanly. Querying a Spark view through Trino's Delta Lake connector produces `UNSUPPORTED_TABLE_TYPE`.

### The user-facing Trino is read-only by intentional design

Per `ansible/roles/trino/templates/values.yaml.j2` and the comment at `ansible/roles/scout_common/defaults/main.yaml:185`, the user-facing Trino in `scout-analytics` enforces read-only at three layers:

- `delta.security=READ_ONLY` on the Delta connector
- `hive_metastore_endpoint_readonly` (separate metastore Deployment with read-only PostgreSQL role)
- `s3_lake_reader` credentials (read-only MinIO bucket policy)

Even if Trino had no authentication (which it doesn't — it accepts any caller as user `trino`), the connector and underlying credentials prevent writes. This means notebook users or anyone else inside the cluster who can reach `trino.scout-analytics:8080` cannot mutate Delta tables, metastore objects, or S3 contents.

`CREATE OR REPLACE VIEW` is a metastore write, so it's blocked at all three layers.

### The transformer cannot reuse the existing Trino

To issue view DDL, the transformer would need either:
1. A Trino instance willing to accept its writes, or
2. A way to put Trino-readable views into the metastore without going through Trino.

Option 2 (writing Trino-format view payloads directly via the Hive Thrift API or PostgreSQL) is undocumented and brittle — the on-disk format isn't a stable interface. So the realistic options are about how to provide a writable Trino without compromising the read-only posture for everyone else.

## Decision

**Stand up a separate small Trino Helm release (`trino-rw`) in the `scout-extractor` namespace. Gate it to the hl7-transformer pod via the chart's built-in NetworkPolicy support. Keep the user-facing Trino read-only.**

The transformer's view-creation activity (`add_epic_views` in `dataextraction.py`) will issue DDL against `trino-rw.scout-extractor:8080`. The user-facing Trino's metastore reads pick up the resulting `VIRTUAL_VIEW` rows automatically (both Trinos share the same Hive metastore database; only the metastore *Deployment* differs in PostgreSQL role).

## Alternatives Considered

### Materialize derivative-with-mapping joins as Delta tables instead of views

For each derivative table, write the joined output as `*_curated_epic`, `*_latest_epic`, etc.

**Rejected**: Roughly doubles the storage of derivative tables; the join must be re-materialized on every ingest; downstream users querying the derivative tables would have parallel "with-epic" and "without-epic" copies to choose between. Storage and consistency cost outweighs the simplicity benefit.

### Use Trino's Hive connector with view translation

Add a `hive` catalog alongside `delta` on the existing user-facing Trino, configured with `hive.hive-views.enabled=true` and `hive.delta-lake-catalog-name=delta` for table redirection. Spark-created views would be readable through the Hive catalog.

**Rejected**: Spark and Trino disagree on type representations. Specifically, Spark writes `timestamp(3) with time zone` columns into the view's stored schema, but Hive's type system has no equivalent. Both Trino's modern translator (rejects with "Unsupported Hive type") and legacy translator (silently downgrades to `timestamp(3)`, then fails at read time with `VIEW_IS_STALE` because the projected column type doesn't match) fail on the report tables, which contain multiple timestamp-with-timezone columns. Salvaging the path would require enumerating timestamp columns and emitting explicit `CAST(... AS TIMESTAMP)` expressions in every view body — a fragile, schema-coupled workaround.

### Loosen `delta.security` on the existing Trino, add Trino access control

Drop `delta.security=READ_ONLY`, enable Trino's file-based or system access control to deny writes to non-transformer users.

**Rejected**: Requires real authentication (Trino currently has none — every caller authenticates as user `trino`), which means integrating Keycloak with Trino, distributing per-service tokens, etc. That is a multi-week change with broad blast radius. Compared to standing up a small second Trino with NetworkPolicy gating, the security gain is theoretical (file ACLs vs. K8s NetworkPolicy enforce roughly equivalent boundaries) and the implementation cost is much higher.

### Use Superset virtual datasets, no Trino views

Define the join as a Superset virtual dataset. Superset users see a "table" in Superset's metadata catalog that's actually a saved SQL query Superset injects when the dataset is referenced.

**Rejected**: Virtual datasets are a Superset-side abstraction only — they exist as rows in Superset's PostgreSQL metadata DB, not in the Hive metastore that Trino reads. So they're invisible to every other Trino consumer. Voilà playbooks issuing `SELECT * FROM reports_curated_epic_view` against Trino get a "table not found" error; same for the MCP Trino tool, JupyterHub notebooks using the Trino Python client, and any future direct consumer. Even within Superset, virtual datasets aren't queryable by name in SQL Lab's text editor — they only surface in chart/dashboard authoring. So this approach covers a fraction of one consumer's use cases and leaves the rest with no joined-view access at all.

### Skip Spark views, only create Trino views

Drop the `spark.sql(CREATE VIEW)` call entirely; only create Trino views via the new RW instance. Provide a PySpark helper for notebook users.

**Rejected**: Forces all PySpark consumers to switch from `spark.read.table(...)` to a custom helper. Existing notebooks break. Worse user experience for the JupyterHub side. The current implementation creates **both** views with distinct names (`*_spark_epic_view` for Spark, `*_epic_view` for Trino) so each engine sees a view it can natively consume.

## Consequences

### Positive

- **Existing read-only posture preserved.** No change to the user-facing Trino's authentication or security model. Notebook and Superset users see the same Trino they always have.
- **Security boundary is K8s-enforced and auditable.** The chart's NetworkPolicy gates ingress to trino-rw to only the hl7-transformer pod (port 8080) and Prometheus (port 5556). Defense in depth: Jupyter user pods already have an egress policy that doesn't allow `scout-extractor:8080`.
- **Reuses existing infrastructure.** Same chart, same metastore database, same MinIO storage. The trino-rw release uses `s3_lake_writer` credentials — already provisioned, already trusted by the transformer's Spark side. No new credentials are introduced.
- **Both views available simultaneously.** PySpark users continue using `spark.read.table("default.reports_curated_spark_epic_view")`; SQL Lab users query `default.reports_curated_epic_view`. Each engine sees the view format it understands.

### Negative

- **Two Trino releases to maintain.** Chart upgrades, configuration changes, and operational drift now apply to both. Mitigated by sharing version variables (`trino_helm_chart_version`, `trino_jmx_exporter_version`) so a single Renovate annotation covers both releases per ADR 0015.
- **Resource overhead for an infrequent workload.** trino-rw exists primarily to issue a small batch of DDL per ingest. We size it minimally (1 worker, 512MiB coordinator heap), but it's still a coordinator + worker pod sitting mostly idle.
- **Transformer pod carries TRINO_HOST/PORT env vars.** Even when the activity isn't running, the pod has these. Inert and harmless, but visible in `kubectl describe`.
- **Test environments must mirror the topology** to exercise the RW path. CI's `deploy_and_test` job doesn't deploy `analytics`, so a future feature flag will let the transformer skip the Trino-side view creation in those CI runs without compromising production behavior.

### Comparison to the auth model from ADR 0005

ADR 0005 established that on-prem deployments use static MinIO access keys (because Hadoop S3A can't use a custom STS endpoint). trino-rw inherits that pattern:
- **On-prem**: Reuses `s3_lake_writer` static keys, injected via a Kubernetes Secret (`trino-rw-s3`) and Trino's `${ENV:VAR}` substitution rather than inlined into the catalog ConfigMap.
- **AWS**: Currently not implemented for trino-rw. The user-facing Trino has an IRSA `lake-reader` ServiceAccount; trino-rw would need a parallel `lake-writer` SA. Flagged as future work; the chart's `aws_deployment` branch in `values-rw.yaml.j2` falls through to no S3 credentials, which would fail at runtime if anyone enabled it on AWS without first wiring IRSA.

## Implementation Notes

### Chart configuration

Per `ansible/roles/trino/templates/values-rw.yaml.j2`:
- `fullnameOverride: trino-rw` so Service names match the release name (the chart's default fullname helper produces `trino-rw-trino` because the release name doesn't equal the chart name).
- No `delta.security` setting — the catalog accepts writes.
- `hive.metastore.uri` points at `hive_metastore_endpoint` (writable instance).
- `envFrom: [secretRef: name: trino-rw-s3]`; catalog uses `s3.aws-access-key=${ENV:S3_ACCESS_KEY}` etc.
- `networkPolicy.enabled: true` with explicit ingress allowances for the transformer pod and Prometheus server. The chart automatically allows coordinator↔worker traffic.

### Secret handling

Both the new RW Trino and the existing user-facing Trino now inject S3 credentials via `envFrom` from a Kubernetes Secret rather than inlining them into the catalog config string (which Helm renders into a plaintext ConfigMap). The Secret is created by the deploy task before the helm install. This change applies to both Trino releases for consistency — see `ansible/roles/trino/tasks/deploy.yaml` and `deploy_rw.yaml`.

### CI tests

`tests/network/network-policy-tests.sh` runs in the smoke-test job and verifies the NetworkPolicy denies ingress from pods in `scout-analytics` and from mislabeled pods in `scout-extractor`, while allowing pods labeled `app.kubernetes.io/name: hl7-transformer` in `scout-extractor`. The script uses retry-loops to handle kube-router's ipset reconciliation lag.

### Renovate coverage

Both Trino releases share `trino_helm_chart_version` and `trino_jmx_exporter_version` (in `ansible/group_vars/all/versions.yaml`), each annotated with a `# renovate:` comment per ADR 0015. CVE-driven version bumps apply to both releases automatically. No new annotations were needed for trino-rw.

## Future Considerations

- **AWS IRSA for trino-rw**: When deploying to AWS, a `lake-writer` ServiceAccount and IAM role would replace the static-key path. Mirrors what the user-facing Trino already does with `lake-reader`. Not blocking for current on-prem deployments.
- **Mapping derivation cost**: The view body (`SELECT r.*, m.scout_patient_id, p.resolved_epic_mrn, p.resolved_mpi FROM ...`) joins against the mapping table, which is itself the product of a non-trivial recursive ID resolution. The cost of the *view definition* is negligible; the cost of the *underlying mapping table* is part of the ingest pipeline and is addressed separately.
- **Per-environment toggle**: A `trino_view_enabled` flag (env var on the transformer) lets test environments without trino-rw skip the Trino-side DDL while still creating the Spark view. CI uses this to keep `deploy_and_test` from depending on the analytics playbook.

## References

- ADR 0005: MinIO STS Authentication Decision — establishes the static-key pattern for on-prem
- ADR 0011: Deployment Portability via Layered Architecture — service-mode pattern that informs cross-environment configuration
- ADR 0012: Security Scan Response and Hardening — Traefik security headers, related security posture
- ADR 0015: Dependency CVE Monitoring via Renovate and Dependabot — explains why sharing `trino_helm_chart_version` is sufficient
