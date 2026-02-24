# Scout - Radiology Report Explorer

## Overview

Scout is a distributed data analytics platform designed for intelligent, intuitive exploration of HL7 radiology reports. It processes large volumes of HL7 messages into a Delta Lake using a medallion architecture (bronze → silver), making them accessible through interactive analytics and notebooks.

**Official Documentation**: https://washu-scout.readthedocs.io/en/latest/

## Architecture

Scout is a microservices platform deployed on Kubernetes (K3s) with the following key components:

### User Services
- **Analytics**: Apache Superset for no-code visualizations and SQL queries (powered by Trino)
- **Notebooks**: JupyterHub with PySpark for programmatic data analysis
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
│   │   ├── analytics.yaml     # Trino + Superset
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
├── terraform/                 # Infrastructure as Code (optional)
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
- **Notebooks**: JupyterHub with PySpark
- **Workflow Orchestration**: Temporal
- **Databases**: PostgreSQL (CloudNativePG operator), Cassandra (K8ssandra), Elasticsearch (ECK)
- **Monitoring**: Prometheus, Loki, Grafana
- **Deployment**: Ansible, Helm
- **Languages**: Python (transformers), TypeScript (orchestrator, extractors, launchpad), Ansible (deployment)

## Data Schema

The Delta Lake silver layer contains a `reports` table with HL7 radiology report data:

### Core Fields
- **Metadata**: `source_file`, `updated`, `message_control_id`, `sending_facility`, `version_id`, `message_dt`
- **Patient Info**: `mpi`, `birth_date`, `sex`, `race`, `ethnic_group`, `zip_or_postal_code`, `country`
- **Patient IDs**: `patient_ids` (array of structs), `epic_mrn`, and dynamically-created ID columns per assigning authority
- **Orders**: `orc_2_placer_order_number`, `obr_2_placer_order_number`, `orc_3_filler_order_number`, `obr_3_filler_order_number`
- **Service**: `service_identifier`, `service_name`, `service_coding_system`, `diagnostic_service_id`, `modality` (derived)
- **Timing**: `requested_dt`, `observation_dt`, `observation_end_dt`, `results_report_status_change_dt`, `patient_age` (derived)
- **Personnel**: `principal_result_interpreter`, `assistant_result_interpreter`, `technician` (arrays)
- **Report Content**: `report_text` (full), `report_status`, `study_instance_uid`
- **Parsed Sections**: `report_section_addendum`, `report_section_findings`, `report_section_impression`, `report_section_technician_note`
- **Diagnoses**: `diagnoses` (array of structs with `diagnosis_code`, `diagnosis_code_text`, `diagnosis_code_coding_system`)
- **Partitioning**: `year` (derived from `message_dt`)

See `docs/source/dataschema.md` for complete schema documentation and HL7 field mappings.

## Development Workflow

### Prerequisites
- **Deployment**: Ansible 2.14+, SSH access to target nodes
- **Python Services**: Python 3.8+, PySpark 3.5.4
- **TypeScript Services**: Node.js/npm
- **Cluster Access**: kubectl configured for K3s cluster
- **Optional**: Docker (local containerization), Terraform (IaC)

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
make install-analytics        # Trino + Superset

# Processing
make install-orchestrator     # Temporal + Cassandra + Elasticsearch
make install-extractor        # HL7 extractors and transformers

# User services
make install-jupyter          # JupyterHub with PySpark
make install-launchpad        # Landing page web UI
make install-chat             # Open WebUI + Ollama (optional)

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
  "deltaIngestTimeout": 120                   // Transform timeout (minutes)
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
- `proxy-public.jupyter.svc.cluster.local`
- `grafana.grafana.svc.cluster.local`
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
3. Use PySpark to query Delta Lake:
   ```python
   from pyspark.sql import SparkSession
   spark = SparkSession.builder.getOrCreate()
   df = spark.read.table("delta.default.reports")
   df.filter(df.modality == "MRI").show()
   ```
4. Export results: `df.toPandas().to_csv("results.csv")`

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
make install-analytics
```

## Testing

### Integration Tests

#### Ingest Tests
Located in `tests/ingest/` - test end-to-end ingestion workflows with Temporal

#### Auth Tests
Located in `tests/auth/` - Playwright browser-based authorization tests for OAuth2 Proxy + Keycloak

### Unit Tests
- **Python** (hl7-transformer): `pytest` in `extractor/hl7-transformer/`
- **Ansible filters**: `uvx pytest tests/unit/filter_plugins/ -v`

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

### PySpark in JupyterHub
- Filter array columns with `F.exists()`: `df.filter(F.exists("diagnoses", lambda x: x.diagnosis_code == "J18.9"))`
- Use `patient_ids` array or convenience columns like `epic_mrn`

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

- **ADR 0002: K3s Air-Gapped Deployment Strategy** — K3s installation in air-gapped environments uses Harbor pull-through proxy for images and a Kubernetes Job on staging to download SELinux RPMs. Consult when modifying k3s installation or the `air_gapped` feature flag behavior.

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
- **Add HL7 field**: Update `extractor/hl7-transformer/` parser, update `docs/source/dataschema.md`
- **Modify workflow**: Edit TypeScript in `orchestrator/`, redeploy extractor role
- **Adjust resources**: Override in `inventory.yaml` (JVM heap, CPU, memory, storage)
- **Add dashboard**: Create in Grafana UI, export JSON to `ansible/roles/grafana/files/dashboards/`
- **Update dependency versions**: Edit `ansible/group_vars/all/versions.yaml`, redeploy component
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
