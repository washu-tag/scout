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

- **`enable_xnat`**: Enable the XNAT imaging platform (`xnatworks/xnat-web`) with plugins
  - Default: `false` (disabled)
  - When false, NOTHING XNAT is created — no namespace/deploy, and the Keycloak realm omits the `xnat` client + `xnat-access` role
  - Requires secrets: `keycloak_xnat_client_secret`, `xnat_postgres_password`, `xnat_admin_password` (the deploy asserts all three and fails if any is empty)
  - `xnat_admin_password` seeds the `admin` account at first boot via `prefs-init.ini`'s `[system] defaultAdminPassword`, so the default `admin:admin` never survives a fresh deploy; it's a first-boot seed only — rotate afterward through the admin UI. Use a strong, vault-encrypted value.
  - First boot is non-interactive: a templated `prefs-init.ini` (mounted from the `xnat-prefs-init` Secret) carries `initialized=true`, site URL, SSO-only `enabledProviders=['keycloak']`, SMTP, and the admin password — no setup wizard. Day-2 preference changes go through the UI/API, not this file.
  - Single-node posture: `replicaCount: 1`, Redis/ActiveMQ off, PVCs via `xnat_storage_class` (empty → cluster default `local-path`, node-pinned), PostgreSQL via CloudNativePG, mail via Scout's shared relay
  - Features: oauth2-proxy edge gate (the only enforced AuthZ layer) + the off-the-shelf `xnat-openid-auth-plugin` for Keycloak SSO; `xnat-access` is provisioned but unenforced (the 1.5.0 plugin can't consume the role). Plugins installed from coordinates/url/image/file via the `xnat-plugin-installer` init container (with logback→stdout rewrite); plugins are additive over the role default via `xnat_plugins`
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

## Architecture Decision Records (ADRs)

ADRs in `docs/internal/adr/` document significant architectural decisions. Consult these when working in relevant areas:

- **ADR 0001: Helm Deployment for Air-Gapped Environments** — Uses remote Helm deployment via kubeconfig rather than OCI registry caching or local template rendering. Consult when modifying air-gapped deployment patterns or Helm chart installations.

- **ADR 0002: K3s Air-Gapped Deployment Strategy** — K3s installation in air-gapped environments uses Harbor pull-through proxy for images. The original Kubernetes Job pattern for downloading SELinux RPMs (section 2) has been superseded by ADR 0017's Nexus yum proxy approach. Consult when modifying k3s installation or the `air_gapped` feature flag behavior.

- **ADR 0003: OAuth2 Proxy as Authentication Middleware** — Implements hybrid authentication: OAuth2 Proxy enforces user approval at the ingress layer, while services maintain their own Keycloak OAuth clients for authorization. Consult when modifying authentication flows, adding new protected services, or working with the user approval workflow.

- **ADR 0004: Storage Provisioning Approach** — Migrated from static hostPath PVs to dynamic provisioning with platform-native storage classes. Supports optional multi-disk configurations via `onprem_local_path_multidisk_storage_classes`. Consult when modifying storage configuration or adding persistent services. Note: Jupyter-specific sections superseded by ADR 0006.

- **ADR 0005: MinIO STS Authentication Decision** — Documents why Scout uses static access keys for MinIO instead of STS authentication—Hadoop S3A connector cannot use custom STS endpoints for WebIdentity tokens. Consult when considering credential management changes for S3-compatible storage.

- **ADR 0006: Jupyter Node Pinning and Storage Approach** — Jupyter pods are pinned to GPU nodes (when available) and use local storage instead of NFS because SQLite file locking fails on network filesystems. Consult when modifying JupyterHub storage or scheduling configuration.

- **ADR 0007: Jump Node Architecture** — Separates the Ansible control node (jump node) from the staging node in air-gapped deployments for security—only the jump node has both internet access and production cluster credentials. Consult when modifying air-gapped deployment architecture or firewall requirements.

- **ADR 0008: Ollama Model Distribution in Air-Gapped Environments** — Pre-stages Ollama models to shared NFS storage from the staging cluster; production mounts NFS read-only. Consult when modifying the Chat feature deployment or model management in air-gapped environments.

- **ADR 0009: Open WebUI Content Security Policy** — Implements CSP via Traefik middleware to prevent LLM-generated external resource URLs from exfiltrating data through the user's browser. Consult when modifying Open WebUI security or Traefik middleware configuration.

- **ADR 0010: Open WebUI Link Exfiltration Filter** — Implements an Open WebUI filter function to sanitize external URLs in LLM responses during streaming, preventing link-based data exfiltration that CSP cannot block. Consult when modifying Open WebUI security or filter function configuration.

- **ADR 0011: Deployment Portability via Layered Architecture** — Introduces a three-layer model (Infrastructure, Platform Services, Applications) and service-mode variables (examples: `postgres_mode`, `object_storage_mode`, `redis_mode`) for cross-platform deployment. Consult when adding new services, modifying deployment patterns, or supporting new platforms.

- **ADR 0012: Security Scan Response and Hardening** — Consolidates findings from Tenable Nessus and OWASP ZAP scans, implementing a global Traefik security headers middleware (HSTS, CSP, X-Frame-Options, etc.) to address the majority of findings. Consult when modifying security headers, Traefik middleware configuration, or evaluating future scan results.

- **ADR 0013: Redis Enterprise to Valkey Migration** — Replaces Redis Enterprise Cluster (commercial license) with a single standalone Valkey instance (BSD-3, Linux Foundation). All Scout Redis usage is ephemeral (sessions, cache, pub/sub), so no data migration is needed. Valkey is a Redis OSS 7.2.4 fork with full RESP protocol compatibility — no application code changes required. Implements `redis_mode: standalone` as the new default per ADR 0011. Consult when modifying Redis/Valkey configuration, caching infrastructure, or the `redis_mode` service-mode variable.

- **ADR 0014: Temporary Open WebUI Context Summarization Filter** (Superseded) — Installed a filter to summarize long conversations approaching the context-window limit. Removed in favor of OWUI's native automatic context compaction added in OWUI 0.10.0.

- **ADR 0015: Dependency CVE Monitoring via Renovate and Dependabot** — Uses self-hosted Renovate (custom regex manager) and Dependabot to monitor dependencies for known CVEs only — not routine version updates. Renovate covers `versions.yaml` (Helm charts, Docker images, GitHub releases); Dependabot covers application dependencies (npm, pip, gradle, Dockerfile base images) via GitHub UI settings, plus GitHub Actions version updates via `dependabot.yml`. Consult when adding new dependencies to `versions.yaml` (add `# renovate:` annotation) or configuring Dependabot (GitHub UI: **Settings > Security > Advanced Security**).

- **ADR 0016: Staging Node Certificate Distribution** — Distributes the staging node's self-signed TLS certificate to the production cluster via Ansible, replacing `insecure_skip_verify: true` with explicit CA trust. For containerd/K3s, the cert is placed on host nodes and referenced via `ca_file` in `registries.yaml`. For future containerized services, the cert is mounted from a Kubernetes Secret. Consult when adding services that contact the staging node or modifying air-gapped TLS configuration.

- **ADR 0017: Air-Gapped Package Proxy** — Deploys Sonatype Nexus CE on the staging node alongside Harbor to serve as a pull-through proxy for conda, PyPI, Maven, and RPM packages. Enables Jupyter users to install packages on demand, simplifies RPM installation to standard `dnf` commands, and allows the Jupyter image to be slimmed by removing baked-in packages. Uses per-format proxy URL variables (`conda_proxy_url`, `pip_proxy_url`, etc.) following the service-mode pattern from ADR 0011, with staging cert trust per ADR 0016. Consult when modifying package proxy configuration, Jupyter notebook image contents, RPM installation tasks, or air-gapped package management.

- **ADR 0018: Squid Forward Proxy for Air-Gapped Authentication** — Installs Squid forward proxy as a system package on the staging node with a strict domain allowlist, enabling Keycloak on air-gapped production clusters to reach external IdP OAuth endpoints (Microsoft, GitHub). Uses Keycloak's `spi-connections-http-client-default-proxy-mappings` SPI, configured conditionally when `air_gapped: true` and an external IdP is present. Consult when modifying air-gapped authentication, adding external IdP providers, or extending outbound access from air-gapped clusters.

- **ADR 0019: Read-Write Trino Instance for Transformer-Issued View DDL** — Adds a second Trino deployment (`trino-rw` in `scout-extractor`) that can write metastore objects, used exclusively by the hl7-transformer for `CREATE OR REPLACE VIEW` DDL. Spark-created views aren't readable by Trino's Delta connector (different metastore-row format), so the transformer issues view DDL through Trino directly. NetworkPolicy restricts ingress to the transformer pod + Prometheus; the user-facing `trino-analytics` remains read-only. Consult when modifying view-creation flow, adding new Trino-readable views, or working with the transformer's metastore interactions.

- **ADR 0020: Trino Authorization via OPA with Keycloak Attributes** — Picks OPA (via Trino's in-tree `opa` access-control plugin) as the policy engine and an attribute-driven model (Keycloak User Profile entries; ships `allowed_facilities` + `redact_select_identifiers` by default, with more row-filter dimensions added as one-line inventory edits via `trino_attribute_filters`) over groups. Policy data shape is generic and inventory-driven: `data.attribute_filters` maps each Keycloak attribute to a `{column, optional tables_override}`, `data.filtered_tables` is the shared list every dimension applies to, `data.masked_columns` lists PHI columns. Adding a restriction dimension is one inventory edit (the keycloak role renders user-profile attributes from `trino_attribute_filters`; the opa role consumes the same map). One non-attribute gate: `user_enabled` requires both `enabled: true` AND membership in `scout-user` or `scout-admin` — the approved-group set is hardcoded in `policy/trino/main.rego` because the same group names are hardcoded in the Keycloak realm template. This catches federated users who landed in Keycloak via upstream OIDC before going through Scout's approval flow. `delta.security=READ_ONLY` is retained at the connector as defense in depth. Consult when modifying the OPA policy (`policy/trino/main.rego`), adding AuthZ dimensions, changing the approval-group set, or reasoning about why OPA was picked over Ranger / file-based / GroupProvider.

- **ADR 0021: OPA User Attribute Distribution via MinIO Bundles** — Defines how per-user attributes reach OPA. A Keycloak SPI listener (`OpaUserBundlePublisherProvider` in `keycloak/event-listener/`) maintains an in-memory user-attribute snapshot, debounces admin events into S3 PUTs of a tar.gz bundle to a dedicated MinIO bucket (`opa-bundles`), and OPA's native bundle plugin pulls every 5–10s and atomically swaps the `data.users` subtree. The Rego policy reads `data.users[user]` directly — no `http.send`, no admin-token client. Per-user payload carries `enabled` + `groups` (synthesized from `UserModel.isEnabled()` / `getGroupsStream()`) plus the User Profile attribute map verbatim. The listener handles both `USER` and `GROUP_MEMBERSHIP` admin events so add/remove from `scout-user` propagates. Consult when modifying the bundle publisher, OPA's bundle plugin config, or the rego's user-attribute lookup path.

- **ADR 0022: Trino Authentication and Identity Propagation** — Picks `http-server.authentication.type=JWT` on Trino's HTTPS listener and the per-client identity-propagation patterns: JupyterHub uses JWT pass-through via `auth_state` (kernel runs as the user, no impersonation); Superset, Voila, and report-viewer each authenticate as a dedicated Keycloak service principal (`superset_svc`, `voila_svc`, `report_viewer_svc`) and impersonate the end user via `X-Trino-User`. Open WebUI never calls Trino directly; its tool runtime POSTs to report-viewer, which carries the trust boundary. Service-principal access tokens issued at 14400s (~4h). Notebook image excludes Spark — every read goes through Trino. Consult when modifying identity propagation in any client role (jupyter, superset, voila, report_viewer, trino), touching the `trino-audience` Keycloak client scope, or rotating service-principal secrets.

- **ADR 0023: Trino View Security Model** — The `_epic_view` family is created `SECURITY DEFINER` so the view owner's underlying-table reads bypass row filters and column masks (which would otherwise clamp to zero rows / NULL out window-function results). The OPA policy carves out three pieces: `data.view_owner_principals` (members bypass row-filter and column-mask evaluation), `data.hidden_tables` (denied for direct SELECT and hidden from `SHOW TABLES`, e.g. `reports_report_patient_mapping`), and a `CreateViewWithSelectFromColumns` allow rule that lets the view owner join through `hidden_tables` while invoker queries against the same tables fail. Column masks are type-aware (varchar→`'[REDACTED]'`, other→bare `NULL`). Consult when adding views over `reports_report_patient_mapping` (or similar facility-less join targets), touching `trino_view_owner_principals` / `trino_hidden_tables`, or reasoning about why the policy looks like it's exempting things from its own AuthZ.

- **ADR 0024: Token Refresh for SDK Trino Access** — Keeps the short-lived bearer (ADR 0022) valid for notebook/playbook code without OAuth in user code, via four layers: (1) custodian-side proactive rotation — Voila re-mints `voila_svc`; Jupyter relies on an oauthenticator `refresh_user_hook` (`eagerTokenRefresh`) that rotates at token half-life because stock oauthenticator only re-validates a live token without rotating; (2) SDK proactive re-fetch in the last ⅕ of the token's lifetime (`_REFRESH_BEFORE_EXPIRY_FRACTION`; lifetime = `exp − iat`, fixed-60s fallback if no `iat`) via a per-request dynamic bearer (one long-lived SQLAlchemy engine survives rotation, no `engine.dispose()`); (3) SDK reactive — `query()` retries once on Trino 401 (bearer rejected), dropping the cached bearer first; 403 (authz denial) is not retried; `connect()` doesn't wrap caller-driven execution; (4) per-kernel `threading.Lock` single-flight. Everything is a fraction of the lifespan (hook rotates at ½, SDK refreshes at ⅕, gate `auth_refresh_age = keycloak_access_token_lifespan // 5` ≤ ½−⅕ = 3⁄10), so it's scale-invariant — no floor, no per-value tuning. `keycloak_access_token_lifespan` drives both the realm `accessTokenLifespan` and the Hub gate so they can't drift. Consult when touching the SDK's `_query.py`/`_identity.py` refresh logic, the Jupyter `eagerTokenRefresh` hook / `auth_refresh_age`, or debugging Trino `JWT expired`/401 from notebooks.

- **ADR 0025: In-App User Administration and Launchpad Token Pass-Through** — Moves user administration out of the Keycloak master console into a `scout-admin`-gated console in the launchpad (`/admin/users`, Next.js), backed by a `scout-users` Keycloak REST resource (a `RealmResourceProvider` in the event-listener SPI). The API approves pending users, edits data-access attributes, promotes/demotes `scout-admin`, and offboards (full revocation of both groups). Authorization is a live `scout-admin` group check plus an `aud=scout-users-api` requirement — no standing admin credential. The attribute schema is **discovered at request time** from User Profile entries annotated `scoutAuthz=true` (rendered from `trino_attribute_filters`), so adding an AuthZ dimension stays the one-line inventory edit ADR 0020 promised. Server-side guardrails (mutation-tested, package-private predicates): no self-offboard, attribute-key allowlist, all-or-nothing validation. Every SPI mutation explicitly emits a Keycloak `AdminEvent` because direct model edits (`joinGroup`/`setAttribute`) fire none — without it OPA (ADR 0021) and the approval/offboard emails wouldn't see SPI-driven changes. Auth uses ADR 0022's same-realm JWT pass-through (next-auth is the fresh-token custodian; a server-side `/api/users/[...path]` route mints a fresh access token per request and forwards it as Bearer) — no token exchange, no `X-Trino-User`. The launchpad client is `fullScopeAllowed=false` (tokens carry only its client roles, not realm-admin); the session cookie holds only the refresh token + `username`/`isAdmin` (JWE/httpOnly/secure), the access token never cached. Consult when modifying the launchpad admin console, the `scout-users` SPI/RealmResource, the discovered attribute schema, SPI admin-event emission, or the launchpad↔Keycloak token pass-through.

- **ADR 0026: XNAT Deployment Posture and Lifecycle** (Proposed) — Deploys XNAT (`nrgxnat/xnat`; published as `xnatworks/xnat-web`) as an opt-in single-node service behind one `enable_xnat` flag (default `false`); when off, the playbook ends before the role and the Keycloak realm omits the `xnat` client + `xnat-access` role, and XNAT secrets (`keycloak_xnat_client_secret`, `xnat_postgres_password`, `xnat_admin_password`) are only required when on. Disabling is destructive (deletes the `xnat` client, orphaning provisioned users — accepted); as shipped `false` *skips* the play rather than tearing down (active teardown preserving namespace/PVCs is an open follow-up). First boot is non-interactive via a templated `prefs-init.ini` (mounted from the `xnat-prefs-init` Secret) carrying `initialized=true`, site URL, enabled providers, SMTP, and a `[system] defaultAdminPassword` from the required `xnat_admin_password` — so the default `admin:admin` never survives a fresh deploy (replaces the earlier post-deploy re-credential job). `prefs-init.ini` is a first-boot seed only; day-2 changes go through UI/API (`prefs-override.ini` noted as future). Login surface is SSO-only (`enabledProviders=['keycloak']`). Auth follows the standard Scout pattern (ADR 0003/0012): oauth2-proxy edge gate + `security-headers`, then the `xnat-openid-auth-plugin` running its own OIDC flow against a confidential `xnat` client — but the **edge gate is the only enforced AuthZ layer** (the off-the-shelf 1.5.0 plugin can't consume the `xnat-access` role, and `forceUserCreate` auto-creates accounts), so `xnat-access` is provisioned but unenforced. Single-node posture: `replicaCount: 1`, Redis/ActiveMQ off, PVCs via `xnat_storage_class` (empty → cluster default `local-path`, node-pinned), PostgreSQL via CloudNativePG, mail via Scout's shared relay (a placeholder `postfix-password` Secret works around the chart's unconditional Postfix subchart). Consult when modifying the xnat role, the `enable_xnat` flag, first-boot/prefs config, or XNAT's auth/storage/DB posture.

- **ADR 0027: XNAT Plugin Delivery** (Proposed) — Installs XNAT plugins through a Scout-built `xnat-plugin-installer` init container that acquires each JAR from one of four source types — Secret-mounted JAR, URL download, image-baked (chart-native `plugins:`), or Maven/Gradle coordinates (`groupId:artifactId:version[:packaging[:classifier]]`; the openid plugin is `...:jar:xpl`) — and rewrites each plugin's bundled Logback config from a rolling **file** appender to a **console** appender so logs hit stdout/Loki (replacing the XNAT team's per-plugin image practice). The Secret/URL/coordinate patterns flow through the rewrite; image-baked bypasses it. The required `xnat-openid-auth-plugin` is a role default and operator lists are **additive** (`xnat_plugins_all = xnat_plugins_default + xnat_plugins`), so SSO can't be dropped. The installer image is a generic Maven runner with no repo/air-gap knowledge baked in: it consumes a mounted `settings.xml` and CA only if present. Repo policy lives in the role from two triggers — a per-plugin `repo_url` (set only for non-Central artifacts; openid is NrgXnat Artifactory `libs-release` only) and air-gapped mode (mirror `*` through the Nexus `scout-maven` group, extending ADR 0017; staging CA per ADR 0016 imported into the JVM truststore). Air-gapped URL-pattern plugins fail fast with guidance to use coordinates/image/file (Nexus raw-repo restaging deferred — no consumer). Consult when modifying the xnat-plugin-installer, the plugin source patterns, the Logback rewrite, `xnat_plugins`/`xnat_plugins_default`, or coordinate resolution / air-gapped Maven config.

- **ADR 0029: Report Viewer Service for Chat** — A FastAPI + React SPA microservice in `scout-analytics` that gives Open WebUI chat a browse/sort/filter/export surface for large cohorts, keeping the rows out of the LLM context. It exposes three LLM-facing OWUI tools over REST (`scout_find_reports` saves a search; `scout_get_reports` and `scout_query_sql` are transient), stores searches as SQL plus metadata, re-evaluates them against Trino on each read, and renders results in an iframe. Auth follows ADR 0022: inbound via the oauth2-proxy identity header (SPA/iframe) or a validated Keycloak Bearer (OWUI tool runtime); outbound as `report_viewer_svc` impersonating the user via `X-Trino-User`. A new-user webhook seeds the OWUI iframe-sandbox flags through a least-privilege Postgres role. Supersedes Open WebUI's Trino-access parts of ADR 0019, 0020, and 0022: the MCP Trino tool and `openwebui_mcp_svc` are gone, and Trino runs JWT-only. Consult when modifying the report-viewer service, its tools/endpoints, the iframe or CSV export, its Trino auth, or the new-user webhook.

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
