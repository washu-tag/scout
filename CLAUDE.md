# Scout - Radiology Report Explorer

## Overview

Scout is a distributed data analytics platform designed for intelligent, intuitive exploration of HL7 radiology reports. It processes large volumes of HL7 messages into a Delta Lake using a medallion architecture (bronze → silver), making them accessible through interactive analytics and notebooks.

**Official Documentation**: https://washu-scout.readthedocs.io/en/latest/

## Architecture

Scout is a microservices platform deployed on Kubernetes (K3s) with the following key components:

### User Services
- **Analytics**: Apache Superset for no-code visualizations and SQL queries (powered by Trino)
- **Notebooks**: JupyterHub; notebooks query Trino via the bundled `scout` SDK (no Spark in the image) for programmatic data analysis
- **Launchpad**: Web-based landing page to access all Scout services
- **Chat** (optional): Open WebUI with Ollama for AI-powered natural language querying

### Data Layer (Lake)
- **MinIO**: S3-compatible object storage (data persistence)
- **Hive Metastore**: Catalog metadata management
- **Delta Lake**: Lakehouse format for ACID transactions and versioning
- **Trino**: Distributed SQL query engine connecting analytics to the lake

### Processing Pipeline
- **Orchestrator**: Temporal workflow engine for coordinating data ingestion
- **Extractor Services**:
  - `hl7log-extractor`: Splits HL7 log files, uploads messages to MinIO (bronze layer)
  - `hl7-transformer`: Parses HL7, transforms to structured data, writes to Delta Lake (silver layer)

### Infrastructure
- **Databases**: PostgreSQL (apps), Cassandra (Temporal persistence), Elasticsearch (Temporal visibility), Redis (caching & websockets)
- **Monitoring**: Prometheus (metrics), Loki (logs), Grafana (dashboards & visualization)
- **Ingress**: Traefik (load balancing and routing)
- **GPU Support** (optional): NVIDIA GPU Operator for accelerated workloads

### Data Flow
```
HL7 Log Files → Orchestrator (Temporal)
                     ↓
            hl7log-extractor → MinIO (Bronze: Raw HL7)
                     ↓
            hl7-transformer → Delta Lake (Silver: Structured)
                     ↓
                  Trino ← Superset & JupyterHub (Query & Analysis)
```

## Project Structure

```
scout/
├── ansible/                    # Deployment automation
│   ├── playbooks/             # Service deployment orchestration
│   │   ├── main.yaml          # Full deployment workflow
│   │   ├── k3s.yaml           # Kubernetes setup
│   │   ├── lake.yaml          # MinIO + Hive + Delta Lake
│   │   ├── trino.yaml         # OPA + Trino
│   │   ├── superset.yaml      # Superset
│   │   ├── orchestrator.yaml  # Temporal + Cassandra + Elasticsearch
│   │   ├── extractor.yaml     # HL7 processors
│   │   ├── jupyter.yaml       # JupyterHub
│   │   ├── monitor.yaml       # Prometheus + Loki + Grafana
│   │   ├── launchpad.yaml     # Landing page
│   │   └── chatbot.yaml       # Open WebUI + Ollama
│   ├── roles/                 # Ansible roles (one per component)
│   │   ├── scout_common/      # Shared defaults, tasks, filters
│   │   ├── minio/
│   │   ├── hive/
│   │   ├── trino/
│   │   ├── superset/
│   │   ├── cassandra/
│   │   ├── elasticsearch/
│   │   ├── temporal/
│   │   ├── extractor/
│   │   ├── jupyter/
│   │   ├── open-webui/
│   │   ├── postgres/
│   │   ├── prometheus/
│   │   ├── loki/
│   │   ├── grafana/
│   │   ├── launchpad/
│   │   ├── xnat/              # XNAT imaging platform + plugins (optional)
│   │   └── gpu-operator/
│   ├── filter_plugins/        # Custom Jinja2 filters (jvm_memory_to_k8s, etc.)
│   ├── group_vars/all/        # Centralized version management
│   ├── inventory.yaml         # Deployment configuration (user-created from example)
│   └── Makefile               # Deployment targets
├── docs/                      # Sphinx documentation
│   ├── source/                # User-facing documentation
│   │   ├── index.md           # Overview & quickstart
│   │   ├── services.md        # Architecture & services
│   │   ├── dataschema.md      # Delta Lake table schema
│   │   ├── ingest.md          # Ingestion workflow
│   │   └── tips.md            # Usage tips
│   └── internal/              # Developer documentation
├── launchpad/                 # React landing page (TypeScript/Node.js)
├── extractor/                 # HL7 processing services
│   ├── hl7log-extractor/      # Splits logs, uploads HL7 (TypeScript/Node.js)
│   └── hl7-transformer/       # Transforms HL7 to Delta (Python/PySpark)
│       └── pyproject.toml     # Package: hl7scout
├── orchestrator/              # Temporal workflows (TypeScript/Node.js)
├── helm/                      # Helm chart configurations
└── tests/                     # Integration and unit tests
    ├── auth/                  # Playwright auth tests (TypeScript/Node.js)
    └── ingest/                # HL7 ingestion integration tests (Java/Gradle)
```

## Key Technologies

- **Container Orchestration**: Kubernetes (K3s lightweight distribution)
- **Data Lake**: Delta Lake on MinIO (S3-compatible object storage)
- **Metadata Catalog**: Apache Hive Metastore
- **Query Engine**: Trino (distributed SQL)
- **Analytics UI**: Apache Superset
- **Notebooks**: JupyterHub (notebooks query Trino via the `scout` SDK)
- **Workflow Orchestration**: Temporal
- **Databases**: PostgreSQL (CloudNativePG operator), Cassandra (K8ssandra), Elasticsearch (ECK)
- **Monitoring**: Prometheus, Loki, Grafana
- **Deployment**: Ansible, Helm
- **Languages**: Python (transformers), TypeScript (orchestrator, extractors, launchpad), Ansible (deployment)

## Data Schema

The Delta Lake silver layer contains a `reports` table with HL7 radiology report data:

### Core Fields
- **Metadata**: `source_file`, `updated`, `content_hash` (internal re-ingest dedup hash), `message_control_id`, `sending_facility`, `version_id`, `message_dt`
- **Patient Info**: `mpi`, `birth_date`, `sex`, `race`, `ethnic_group`, `zip_or_postal_code`, `country`
- **Patient IDs**: `patient_ids` (array of structs), `epic_mrn`, and dynamically-created ID columns per assigning authority
- **Orders**: `orc_2_placer_order_number`, `obr_2_placer_order_number`, `orc_3_filler_order_number`, `obr_3_filler_order_number`
- **Service**: `service_identifier`, `service_name`, `service_coding_system`, `diagnostic_service_id`, `modality` (derived)
- **Timing**: `requested_dt`, `observation_dt`, `observation_end_dt`, `results_report_status_change_dt`
- **Personnel**: `principal_result_interpreter`, `assistant_result_interpreter`, `technician` (arrays)
- **Report Content**: `report_text` (full), `report_status`, `study_instance_uid`
- **Parsed Sections**: `report_section_addendum`, `report_section_findings`, `report_section_impression`, `report_section_technician_note`
- **Diagnoses**: `diagnoses` (array of structs with `diagnosis_code`, `diagnosis_code_text`, `diagnosis_code_coding_system`)
- **Partitioning**: `year` (derived from `message_dt`)

See `docs/source/dataschema.md` for complete schema documentation and HL7 field mappings.

## Development Workflow

### Prerequisites
- **Deployment**: Ansible 2.14+, SSH access to target nodes
- **Python Services**: Python 3.10+, PySpark 4.1.1
- **TypeScript Services**: Node.js/npm
- **Cluster Access**: kubectl configured for K3s cluster
- **Optional**: Docker (local containerization)

### Deployment Commands

All deployment is done via Ansible from the `ansible/` directory:

```bash
# Full deployment
make all                      # Deploy entire Scout platform

# Infrastructure
make install-k3s              # K3s + Traefik + GPU operator (if configured)
make install-postgres         # PostgreSQL (CloudNativePG)

# Data layer
make install-lake             # MinIO + Hive Metastore

# Analytics
make install-trino            # OPA + Trino
make install-superset         # Superset

# Processing
make install-orchestrator     # Temporal + Cassandra + Elasticsearch
make install-extractor        # HL7 extractors and transformers

# User services
make install-jupyter          # JupyterHub (notebooks query Trino via the scout SDK)
make install-launchpad        # Landing page web UI
make install-chat             # Open WebUI + Ollama (optional)
make install-xnat             # XNAT imaging platform + plugins (optional)

# Monitoring
make install-monitor          # Prometheus + Loki + Grafana

# Development/testing services
make install-orthanc          # Orthanc PACS server
make install-dcm4chee         # DCM4CHEE PACS server
make install-mailhog          # Email testing
```

### Configuration

1. **Create inventory**: `cp ansible/inventory.example.yaml ansible/inventory.yaml`
2. **Configure**: Edit `inventory.yaml` for your environment:
   - Hosts (server, workers, GPU nodes, staging)
   - Storage paths (MinIO, PostgreSQL, Cassandra, Ollama, Open WebUI, etc.)
   - Secrets (use Ansible Vault for passwords/tokens)
   - Resources (CPU, memory, storage allocations)
   - Feature flags (e.g., `enable_chat` for optional Chat service)
   - Namespaces (optional overrides)
3. **Deploy**: Run `make all` or individual `make install-*` targets

### Feature Flags

Scout supports optional features that can be enabled via feature flags in `inventory.yaml`:

- **`enable_chat`**: Enable AI-powered chat interface (Open WebUI + Ollama)
  - Default: `false` (disabled)
  - Set to `true` in inventory to enable
  - Requires storage paths: `ollama_dir`, `open_webui_dir`
  - Requires secrets: `open_webui_postgres_password`, `open_webui_secret_key`, `open_webui_redis_password`, `keycloak_open_webui_client_secret`
  - Features: Keycloak OAuth authentication, Trino MCP tool for natural language SQL queries, Redis-based websocket coordination
  - Recommended: GPU node for optimal performance
  - Post-deployment configuration required (see `ansible/roles/open-webui/README.md`)

- **`enable_xnat`**: Enable the XNAT imaging platform (`ghcr.io/nrgxnat/xnat`) with plugins
  - Default: `false` (disabled)
  - When false, NOTHING XNAT is created — no namespace/deploy, and the Keycloak realm omits the `xnat` client + `xnat-access` role
  - Requires secrets: `keycloak_xnat_client_secret`, `xnat_postgres_password`, `xnat_admin_password` (the deploy asserts all three and fails if any is empty)
  - `xnat_admin_password` seeds the `admin` account at first boot via `prefs-init.ini`'s `[system] defaultAdminPassword`, so the default `admin:admin` never survives a fresh deploy; it's a first-boot seed only — rotate afterward through the admin UI. Use a strong, vault-encrypted value.
  - First boot is non-interactive: a templated `prefs-init.ini` (mounted from the `xnat-prefs-init` Secret) carries `initialized=true`, site URL, SSO-only `enabledProviders=['keycloak']`, SMTP, and the admin password — no setup wizard. Day-2 preference changes go through the UI/API, not this file.
  - Single-node posture: `replicaCount: 1`, Redis/ActiveMQ off, PVCs via `xnat_storage_class` (empty → cluster default `local-path`, node-pinned), PostgreSQL via CloudNativePG, mail via Scout's shared relay
  - Features: oauth2-proxy edge gate (the only enforced AuthZ layer) + the off-the-shelf `xnat-openid-auth-plugin` for Keycloak SSO; `xnat-access` is provisioned but unenforced (the 1.5.0 plugin can't consume the role). Plugins installed from coordinates/url/image/file via the chart's native plugin installer; plugins are additive over the role default via `xnat_plugins`
  - Caveat: toggling back to `false` deletes the `xnat` Keycloak client, orphaning provisioned XNAT users (and as shipped, `false` skips the play — an already-deployed XNAT keeps running until manually removed)
  - See `ansible/roles/xnat/README.md`, and ADRs 0026 (deployment posture/lifecycle) and 0027 (plugin delivery)

### Variable Precedence

Configuration hierarchy (lowest to highest precedence):
1. **Role defaults** (`roles/*/defaults/main.yaml`) - Component-specific defaults
2. **Common defaults** (`roles/scout_common/defaults/main.yaml`) - Shared Scout defaults
3. **Inventory vars** (`inventory.yaml`) - **Your customizations go here**
4. **Group vars** (`group_vars/all/versions.yaml`) - Version management (higher than inventory)
5. **Extra vars** (`-e` flag) - Highest precedence

**Key point**: You can override most defaults in `inventory.yaml`, but component versions in `group_vars/all/versions.yaml` take precedence (use `-e` flag to override for testing).

### Local Development

Each service directory has its own development setup:
- **launchpad/**: React app (`npm install`, `npm start`)
- **orchestrator/**: Temporal workflows (`npm install`, deploy to cluster)
- **extractor/hl7log-extractor/**: TypeScript service
- **extractor/hl7-transformer/**: Python package `hl7scout` (PySpark)

## Ingestion Workflow

HL7 reports are ingested via Temporal workflows:

### Workflow Steps
1. **Submit** workflow to Temporal (via CLI, UI, or SDK)
2. **Extract**: `hl7log-extractor` activity splits log files into individual HL7 messages, uploads to MinIO (bronze)
3. **Transform**: `hl7-transformer` activity parses HL7, applies transformations, writes to Delta Lake (silver)
4. **Query**: Data immediately available via Trino in Superset and JupyterHub

### Workflow Input Parameters

```json
{
  "date": "YYYYMMDD",                         // Optional: filter logs by date
  "logPaths": ["path/to/file.log"],           // Optional: specific log files
  "logsRootPath": "/data/hl7",                // Root path to search for logs
  "scratchSpaceRootPath": "/tmp/scout",       // Temp files during processing
  "hl7OutputPath": "s3://bucket/hl7",         // Bronze layer S3 path
  "reportTableName": "reports",               // Delta Lake table name
  "splitAndUploadTimeout": 120,               // Activity timeout (minutes)
  "splitAndUploadHeartbeatTimeout": 10,       // Heartbeat timeout (minutes)
  "splitAndUploadConcurrency": 4,             // Concurrent log processing
  "deltaIngestTimeout": 120,                  // Base-ingest activity timeout (minutes)
  "deriveDeltaTablesTimeout": 120             // Derivative-table activity timeout (minutes)
}
```

Omitted parameters default to Ansible inventory variables.

### Launching Workflows

**Via Temporal CLI (admintools container):**
```bash
kubectl exec -n temporal -i deployment/temporal-admintools -- temporal workflow start \
  --task-queue ingest-hl7-log \
  --type IngestHl7LogWorkflow \
  --input '{"logsRootPath": "/data/hl7", "reportTableName": "reports"}'
```

**Via Temporal UI:**
1. Access Temporal Web UI
2. Click "Start Workflow"
3. Fill form:
   - Workflow ID: Random UUID
   - Task Queue: `ingest-hl7-log`
   - Workflow Type: `IngestHl7LogWorkflow`
   - Input > Data: JSON parameters above
   - Input > Encoding: `json/plain`

See `docs/source/ingest.md` for detailed ingestion documentation.

## Monitoring & Observability

Scout includes comprehensive monitoring via Grafana:

### Pre-configured Dashboards
- **Kubernetes**: Cluster health, node metrics, pod status
- **Temporal**: Workflow execution, activity metrics, task queues
- **MinIO**: Storage usage, API performance
- **Databases**: PostgreSQL, Cassandra performance
- **HL7 Ingest**: Extractor status, ingestion rates, errors
- **Applications**: Trino, Superset, JupyterHub metrics

### Accessing Grafana
Grafana is accessible within the cluster via the Kubernetes service. Access methods depend on your deployment:
- **Ingress**: If configured with `external_url` in inventory, access via your domain
- **Internal**: From within the cluster network

### Usage Tips (from docs/source/tips.md)
- **Dashboards**: Located in Grafana under **Dashboards > Scout**
- **Logs**: Access via **Drilldown > Logs** section
- **Time Ranges**: Adjust time range to focus on specific periods
- **Legend Filtering**: Click legend entries to isolate specific metrics/logs
- **Variables**: Use dashboard variables (namespace, node, etc.) for filtering
- **Correlating Logs**: Select "Include" for multiple services, click "Show Logs"
- **Disk Usage**: Use **Node Exporter** dashboard (PV/PVC metrics may not work on-prem)
- **Saving Changes**: Provisioned dashboards can't be edited directly; save as new dashboard, export JSON, commit to repo

### Log Aggregation
- All service logs collected by Loki
- Searchable and filterable in Grafana Explore
- Structured logging with contextual metadata
- Drilldown from metrics to related logs

## Accessing Services

Scout services are accessible within the Kubernetes cluster. Access methods:

### Via Ingress (Production)
If configured with `external_url` in `inventory.yaml` and DNS/TLS setup:
- **Launchpad** (landing page): `https://<external_url>/`
- **Superset**: Via Launchpad or `https://<external_url>/superset`
- **JupyterHub**: Via Launchpad or `https://<external_url>/jupyter`
- **Grafana**: Via Launchpad or `https://<external_url>/grafana`
- **Temporal UI**: Via Launchpad or `https://<external_url>/temporal`

### From Within Cluster
Services communicate via Kubernetes service names:
- `superset.<namespace>.svc.cluster.local`
- `grafana.<namespace>.svc.cluster.local`
- etc.

## Common Tasks

### Query Reports in Superset
1. Navigate to Scout Analytics (Superset)
2. Use **SQL Lab** with Trino connection
3. Query table: `delta.default.reports`
4. Example: `SELECT * FROM delta.default.reports WHERE modality = 'CT' LIMIT 100`
5. Create visualizations and dashboards from query results

### Analyze Data in JupyterHub
1. Access Scout Notebooks (JupyterHub)
2. Open provided quickstart: `Scout/Quickstart.ipynb`
3. Query the lake through Trino with the bundled `scout` SDK (the notebook image
   has no Spark — every read goes through Trino as the logged-in user, so
   per-user row filters and column masks apply; see ADR 0022):
   ```python
   import scout
   df = scout.query("SELECT * FROM reports WHERE modality = :m", params={"m": "MRI"})
   ```
   `scout.query()` returns a pandas DataFrame; `scout.connect()` gives a DB-API
   connection for streaming/large results.
4. Export results: `df.to_csv("results.csv")`

### Monitor Ingestion
1. Access Grafana
2. Navigate to **Dashboards > Scout > HL7 Ingest Dashboard**
3. Check Temporal UI for workflow execution details
4. View logs in **Grafana > Explore > Loki**

### Troubleshoot Issues
```bash
# Check pod status across all namespaces
kubectl get pods -A

# View logs for specific pod
kubectl logs -n <namespace> <pod-name>

# Check recent logs with follow
kubectl logs -n temporal <temporal-worker-pod> -f

# Describe pod for events
kubectl describe pod -n <namespace> <pod-name>

# Verify Ansible configuration
ansible-inventory -i inventory.yaml --list
ansible-inventory -i inventory.yaml --host <hostname>

# Re-run deployment with check mode (dry run)
ANSIBLE_CMD="--check --diff" make install-<component>

# Re-deploy specific component
make install-trino
```

## Testing

### Integration Tests

#### Ingest Tests
Located in `tests/ingest/` - test end-to-end ingestion workflows with Temporal

#### Auth Tests
Located in `tests/auth/` - Playwright browser-based authorization tests for OAuth2 Proxy + Keycloak

### Unit Tests
- **Python** (hl7-transformer): `pytest` in `extractor/hl7-transformer/`

### Ansible Role Testing
- **Molecule**: Test Ansible roles in isolation
- See `docs/internal/molecule_ansible_testing.md`

## Air-Gapped Deployment

Scout supports deployment in air-gapped (offline) environments:

### Architecture
1. **Staging node**: Internet-connected K3s cluster with Harbor registry proxy
2. **Production cluster**: Air-gapped K3s that pulls images from Harbor
3. **Registry mirrors**: Harbor caches container images from upstream registries

### Setup
1. Define `staging` group in `inventory.yaml`
2. Set `air_gapped: true` in inventory
3. Deploy staging: `make install-staging` (or `ansible-playbook playbooks/staging.yaml`)
4. Deploy Scout: `make all` (automatically uses Harbor mirrors)

See `ansible/README.md` and `docs/internal/air-gapped-helm-remote-deployment-adr.md` for details.

## Custom Ansible Filter Plugins

Scout includes custom Jinja2 filters for complex transformations:

### `jvm_memory_to_k8s`
Converts JVM heap sizes (decimal) to Kubernetes memory (binary) with optional multiplier:
```yaml
memory: "{{ cassandra_max_heap | jvm_memory_to_k8s }}"      # "2G" → "2Gi"
memory: "{{ cassandra_max_heap | jvm_memory_to_k8s(2) }}"   # "2G" → "4Gi" (2x for limits)
```
Used by: Cassandra, Elasticsearch, Trino, HL7 Transformer

### `multiply_memory`
Multiplies memory values while preserving decimal units (for non-K8s configs):
```yaml
memory: "{{ jupyter_spark_memory | multiply_memory(2) }}"   # "8G" → "16G"
```
Used by: JupyterHub (requires decimal, not K8s binary format)

See `ansible/filter_plugins/` and `ansible/README.md` for details and testing.

## Tips & Best Practices

### Query Performance
- Use Trino's columnar format advantages (Delta Lake)
- Filter on partitioned columns (`year`) for better performance
- Use parsed report sections for targeted text analysis

### Querying from Notebooks (scout SDK)
- Use `scout.query(sql, params=...)` with `:name` bind params; it returns a pandas DataFrame. `scout.connect()` returns a Trino DB-API connection for streaming.
- Filter array-of-struct columns with `any_match()`: `WHERE any_match(diagnoses, x -> x.diagnosis_code = 'J18.9')`. For matching a scalar column against a list param, prefer `contains(:vals, col)` over `IN` — the SQLAlchemy dialect doesn't expand list params into `IN` clauses.
- Use the `patient_ids` array or convenience columns like `epic_mrn`.

### Monitoring
- Adjust time ranges to match data availability
- Click legend entries to filter/isolate metrics
- Use dashboard variables for targeted analysis
- Correlate logs across services for debugging

### Development
- Test Ansible changes with `--check --diff` before applying
- Component versions managed in `group_vars/all/versions.yaml`
- Override defaults in `inventory.yaml`, not role defaults
- Use `-e` flag to test different versions

## Additional Resources

- **Main Documentation**: https://washu-scout.readthedocs.io/en/latest/
- **Issue Tracker**: https://xnat.atlassian.net/jira/software/projects/SCOUT/summary
- **Ansible Docs**: https://docs.ansible.com/
- **K3s**: https://docs.k3s.io/
- **Temporal**: https://docs.temporal.io/
- **Delta Lake**: https://delta.io/
- **Trino SQL**: https://trino.io/docs/current/language.html
- **Apache Superset**: https://superset.apache.org/docs/
- **JupyterHub**: https://jupyterhub.readthedocs.io/
- **PySpark**: https://spark.apache.org/docs/latest/api/python/

## CI, Versioning, and Release Conventions

Operative rules for changes touching CI, releases, or published artifacts. Design
and rationale live in ADR 0030 (versioning + artifact publishing) and ADR 0031
(Flux deployment base); this section is the working contract.

### Commits and PRs
- PR titles must be [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat`, `fix`, `chore`, `ci`, `docs`, `refactor`, `test`, `perf`, `build`,
  `revert`; a trailing `!` marks a breaking change). The `PR Title Lint` check
  enforces this on every PR. Prefer `fix(scope):` / `feat(scope):` over a bare
  custom type.
- Merges to `main` are squash-merges (ADR 0030): the squashed commit subject is
  the PR title, that title is what release automation reads for the version bump
  and changelog, and the linear history it yields is required by the build-lane
  run-number ordering. Do not merge-commit or rebase-merge `main`.
- Renovate and Dependabot are configured to emit `chore(deps):` titles so their
  PRs satisfy the lint.

### Versioning and releases (ADR 0030)
- Build lane `0.YYYYMMDD.<run>`: minted once per run in the `changes` job. Each
  merge rebuilds only what changed and records the whole platform, pinned by
  `name:tag@digest`, in a signed build manifest. The manifest tooling and its
  schema (the wire contract) live in `tooling/manifest/`.
- Release lane `X.Y.Z`: version + changelog computed from Conventional-Commit
  titles by release-please (`fix` -> patch, `feat` -> minor, `!` -> major). Do
  not hand-type release versions.
- `Chart.yaml` / `VERSION` / `pyproject.toml` version fields are placeholders
  stamped at publish time; do not bump them by hand for a release.

### Artifacts
- Charts and images publish to `oci://ghcr.io/washu-tag/...` only when they
  change (the `changes` job's path filters decide); publishing unchanged content
  needlessly rolls pods.
- Never enable registry auto-pruning (delete-untagged / older-than-N): content is
  pinned by digest under possibly-old tags, so pruning would reap live content.
  Only prune digests that no manifest references.

### CI structure
- The `changes` job's `dorny/paths-filter` block is the single path -> component
  map. Adding an image or chart means adding its filter + output there (and, for
  a new image, an entry in the `&image-matrix` anchor and a
  `<subproject>/.trivyignore.yaml`). See "Add a new CI-built image/service" below.
- `scan-images` fails a non-allow-failure image on any fixable HIGH/CRITICAL CVE
  left after its per-image `.trivyignore.yaml` / `.trivyignore.rego`. Bump the
  dependency where we own it; suppress-with-documented-reason only what an
  upstream base image bundles.
- Superseded PR runs auto-cancel (workflow `concurrency`); `publish` /
  `publish-charts` run only on push to `main`. CI helper code lives in
  `.github/scripts/` and `tooling/`; reusable steps in `.github/actions/`.

## Architecture Decision Records (ADRs)

ADRs live in `docs/internal/adr/` and are authoritative for the areas they cover. The
list below is for routing only — it tells you which file to open, not what it says. Read
the ADR itself before changing anything it covers.

- **0001** air-gapped Helm — installs go out over kubeconfig, not an OCI cache or local render
- **0002** air-gapped K3s — images via Harbor pull-through (SELinux RPM part superseded by 0017)
- **0003** oauth2-proxy — gates at ingress; services keep their own Keycloak clients. Read when adding a protected service
- **0004** storage provisioning — dynamic provisioning + storage classes, optional multidisk (Jupyter parts superseded by 0006)
- **0005** MinIO credentials — why static keys, not STS (S3A can't use a custom STS endpoint)
- **0006** Jupyter placement — pinned to GPU nodes, local storage only (SQLite locking breaks on NFS)
- **0007** jump node — Ansible control node kept separate from the staging node in air-gapped deployments
- **0008** Ollama models — pre-staged to NFS from staging, mounted read-only in prod
- **0009** Open WebUI CSP — Traefik middleware against LLM-driven browser exfiltration
- **0010** Open WebUI link filter — sanitizes external URLs mid-stream, covering what CSP can't
- **0011** layered deployment — three layers + service-mode vars (`postgres_mode`, `redis_mode`, …). Read when adding a service
- **0012** security hardening — global Traefik security-headers middleware; responses to scan findings
- **0013** Valkey — standalone Valkey is `redis_mode: standalone`, the default
- **0014** OWUI summarization filter — **superseded** by native context compaction; historical only
- **0015** dependency CVE monitoring — Renovate watches `versions.yaml` (new entries need a `# renovate:` annotation), Dependabot watches app deps
- **0016** staging cert — the staging self-signed cert is trusted explicitly, not skipped
- **0017** package proxy — Nexus proxies conda/PyPI/Maven/RPM via per-format `*_proxy_url` vars
- **0018** Squid — domain-allowlisted forward proxy so air-gapped Keycloak can reach external IdPs
- **0019** `trino-rw` — a write-capable Trino that exists only for transformer view DDL; user-facing Trino stays read-only
- **0020** Trino AuthZ — OPA + Keycloak attributes; a new restriction dimension is one `trino_attribute_filters` edit. Policy: `policy/trino/main.rego`
- **0021** OPA user data — a Keycloak SPI publishes user-attribute bundles to MinIO; OPA pulls them into `data.users`
- **0022** Trino auth — JWT on Trino; Jupyter passes the user's token through, other clients impersonate via `X-Trino-User`. Read before touching any client's Trino auth
- **0023** view security — the `_epic_view` family is SECURITY DEFINER; OPA carves out view owners and hidden tables
- **0024** SDK token refresh — how short-lived bearers stay valid in notebooks. Read when debugging notebook 401s / `JWT expired`
- **0025** user admin — administration lives in the launchpad `/admin/users` console over a `scout-users` Keycloak SPI resource
- **0026** XNAT posture — what `enable_xnat` creates: secrets, non-interactive first boot, SSO-only login, storage/DB/mail, destructive disable
- **0027** XNAT plugins — **superseded**: plugins install chart-natively; the `xnat-plugin-installer` image is gone
- **0028** HL7 listener — Camel MLLP listener + Kafka batcher, **observer mode only**; nothing downstream consumes `hl7-raw` yet
- **0029** report-viewer — FastAPI + React cohort browser for chat; replaced Open WebUI's MCP Trino tool
- **0030** versioning — build lane `0.YYYYMMDD.<run>` + signed build manifest, release lane `X.Y.Z` from PR titles. Working rules are in the CI section above
- **0031** GitOps — Flux consumes `deploy/`, Ansible shrinks to bootstrap. Read before changing any component's deployment
- **0032** re-ingest gate — `content_hash` makes an unchanged re-ingest a no-op. Read before touching the base merge or OBX ordering

For 0030/0031 start with `docs/internal/adr/0030-0031-tldr.md`; the phased migration plan
is `docs/internal/gitops-implementation-plan.md`.

**When you add an ADR, add exactly one line here**: the decision in a clause, plus the
trigger that should send a reader to the file. This is a routing table, not a set of
summaries — an agent decides from this line whether to open the ADR, and gets every
detail from the ADR. Do not restate config keys, variable names, thresholds, or
rationale; duplicated detail goes stale silently and crowds out the rest of this file. If
a line is growing past one sentence, that is a sign the ADR should be read instead.

## Key Concepts for AI Assistants

### Architecture Understanding
- **Medallion architecture**: Bronze (raw HL7) → Silver (structured Delta Lake) → Query layer (Trino)
- **Orchestration**: Temporal coordinates workflows; activities run in worker pods
- **Separation of concerns**: Extractor splits logs, transformer structures data, Trino queries
- **Object storage**: MinIO provides S3-compatible storage for Delta Lake

### Configuration Management
- **Centralized defaults**: `roles/scout_common/defaults/main.yaml` defines Scout-wide settings
- **Version control**: `group_vars/all/versions.yaml` pins all component versions
- **User overrides**: `inventory.yaml` is where deployment-specific config lives
- **Secrets**: Use Ansible Vault for sensitive values

### Deployment Patterns
- **Idempotent**: Ansible roles can be re-run safely
- **Component isolation**: Each `make install-*` target deploys one logical component
- **Helm-based**: Most services deployed via Helm charts (managed by Ansible)
- **Operator-managed**: PostgreSQL (CloudNativePG), Cassandra (K8ssandra), Elasticsearch (ECK)

### Common Modification Patterns
- **Add HL7 field**: Update `extractor/hl7-transformer/` parser, update `docs/source/dataschema.md`, and update the "Tables & Columns Reference" section in `helm/open-webui-bootstrap/files/payloads/scout-system-prompt.md` so the Scout Explorer model sees the new field (OWUI's RAG auto-injection is bypassed under native function-calling, so schema docs are inlined into the prompt instead of attached as knowledge)
- **Modify workflow**: Edit TypeScript in `orchestrator/`, redeploy extractor role
- **Adjust resources**: Override in `inventory.yaml` (JVM heap, CPU, memory, storage)
- **Add a Grafana dashboard**: Create in Grafana UI, export JSON to `ansible/roles/grafana/files/dashboards/`
- **Add a Superset dashboard, chart, or dataset**: Export the asset YAML from Superset and drop it into `helm/scout-dashboards/files/analytics/<charts|dashboards|datasets/Scout_Data_Lake>/<bundle>/`. New bundles also need their name added to `scout_dashboard_bundles` in inventory. See `helm/scout-dashboards/README.md` for the bundle layout and how to host site-specific dashboards.
- **Update dependency versions**: Edit `ansible/group_vars/all/versions.yaml`, redeploy component
- **Add a new CI-built image/service**: Wiring it into `.github/workflows/ci.yaml` (changes filter, `build-and-upload` matrix, `publish`/`publish-demo`) only covers the `latest` tag on `main`. You MUST also wire the **release path**, or a tagged release ships the image frozen at `latest`: add its entries to `.github/scripts/update-versions.sh` (image-tag ansible var + `build.gradle`/`VERSION` + chart `version`/`appVersion`), the `IMAGES=` list in `.github/workflows/release.yaml`, and the tables in `docs/internal/versions-and-releases.md`.
- **Release new Scout version**: See `docs/internal/versions-and-releases.md` for complete checklist of files to update
- **Configure namespaces**: Override namespace variables in `inventory.yaml`
- **Enable optional features**: Set feature flags in `inventory.yaml` (e.g., `enable_chat: true`), configure required paths and secrets, complete post-deployment setup per role README
- **Add Ansible tasks with kubernetes.core**: See `docs/internal/ansible_roles.md` for kubeconfig configuration conventions (cluster vs jump node execution)

### Debugging Strategy
1. Check pod status: `kubectl get pods -n <namespace>`
2. View logs: `kubectl logs -n <namespace> <pod>`
3. Check Grafana dashboards for metrics
4. View aggregated logs in Grafana > Explore > Loki
5. Check Temporal UI for workflow execution details
6. Verify config: `ansible-inventory -i inventory.yaml --list`

## License

See the main Scout repository for license information.
