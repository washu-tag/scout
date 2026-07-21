# GitOps Transition Analysis: What Ansible Does Beyond Helm

This document catalogs every operation the Ansible deployment performs **beyond** `helm upgrade --install`, organized by category. Each item represents something that must be accounted for in a GitOps migration — either as a manifest in Git, a dependency-ordered resource, a Job wired into the deployment graph, an init container, an operator, or a manual bootstrapping step. See [Section 11](#11-argo-cd-vs-flux-cd-considerations-for-scout) for a detailed comparison of Argo CD vs Flux CD for Scout's specific needs.

> **Status update (2026-07-08):** the decisions this analysis fed are now
> recorded as ADRs. **ADR 0031** adopts the GitOps deployment base and selects
> **Flux** (Section 11's comparison is retained as the analysis of record);
> **ADR 0030** provides the artifact substrate (per-merge OCI charts and
> immutable image tags) that the HelmReleases in that base pin. This document
> remains the operation inventory for the migration. Inventory refreshed the
> same date: roles added since the original writing (OPA — ADR 0020/0021,
> XNAT — ADR 0026/0027, data generator, scout-dashboards) folded in; realm
> template size corrected; the Superset dashboard bundling this doc originally
> recommended chart-izing has since shipped as `helm/scout-dashboards`.

---

## 1. Kubernetes Resource Creation (Non-Helm)

These are raw `kubernetes.core.k8s` resources Ansible creates directly — not managed by any Helm chart. In GitOps, each of these becomes a manifest committed to the repo and synced by Argo CD / Flux.

### 1.1 Secrets

| Role | Secret Name | Contents | Notes |
|------|------------|----------|-------|
| **extractor** | `postgres-secret` | DB host, port, name, user, password | Connects extractor to PostgreSQL |
| **extractor** | `s3-secret` | AWS access key + secret key | MinIO/S3 credentials for bronze layer |
| **grafana** | `smtp-grafana` | SMTP host, user, password | Conditional on `alert_contact_point` |
| **grafana** | `keycloak-grafana-client-secret` | OAuth client secret | Keycloak integration |
| **grafana** | `postgres-ingest-db-secret` | Postgres datasource config for Grafana | Enables direct DB queries in dashboards |
| **grafana** | Contact point secrets (per type) | Slack webhook URL, email SMTP | One Secret per contact point template |
| **hive** | `{instance}-secret` | S3 keys, DB password, Keycloak client secret | Created twice: write + readonly metastore |
| **jupyter** | `staging-ca-cert` | Staging node CA certificate (PEM) | Air-gapped only, for package proxy TLS |
| **keycloak** | `keycloak-db-secret` | DB host, port, name, user, password | Postgres connection for Keycloak |
| **keycloak** | `keycloak-admin-secret` | Admin username + password | Initial admin credentials |
| **keycloak** | `keycloak-config` | `scout-realm.json` (~1,100-line Jinja2 template) | Full realm definition — clients, roles, IdPs, auth flows |
| **keycloak** | `opa-bundle-writer` | S3 access key + secret key | Cross-namespace mirror of the MinIO bundle-writer credentials for the OpaUserBundlePublisher SPI (ADR 0021) |
| **opa** | `opa-bundle-reader` | S3 access key + secret key | OPA bundle plugin pulls user-attribute bundles from MinIO (ADR 0020/0021) |
| **loki** | `loki-secrets` | S3 access key + secret key | Non-AWS only; for MinIO-backed Loki storage |
| **minio** | `minio-env-configuration` | Root user/password, region, OIDC config | Tenant environment config |
| **minio** | Per-user credential secrets | Access key + secret key per MinIO user | lake-reader, lake-writer, loki-writer |
| **oauth2-proxy** | `oauth2-proxy-logo` | Binary PNG data | Custom branding image |
| **oauth2-proxy** | Redis password secret | Valkey auth password | Session store credentials |
| **open-webui** | `open-webui-secrets` | WEBUI_SECRET_KEY, OAUTH_CLIENT_SECRET, REDIS_URL | App secrets |
| **postgres** | `postgres-user` | Basic-auth username + password (kubernetes.io/basic-auth) | CloudNativePG primary user |
| **postgres** | `superuser-secret` | Superuser username + password | CloudNativePG superuser |
| **prometheus** | `jupyterhub-metrics-api-token` | API token | JupyterHub metrics scraping |
| **traefik** | TLS secret | `tls.crt` + `tls.key` (kubernetes.io/tls) | Slurped from remote host paths |
| **valkey** | Auth secret | Valkey password | Shared by all Valkey consumers |
| **nexus** | `nexus-root-password` | Root password | Only created if doesn't already exist |
| **orthanc** | `orthanc-secret` | Full `orthanc.json` configuration (templated) | Embedded application config |
| **dcm4chee** | `dcm4chee-db-secret` | Postgres user + password | Database credentials |
| **xnat** | `xnat-prefs-init` | Templated `prefs-init.ini` | Non-interactive first boot: site URL, SSO-only providers, admin seed (ADR 0026) |
| **xnat** | Per-plugin Secrets (`xnat-plugin-*`) | Plugin JARs / config files | Plugin delivery inputs for the installer init container (ADR 0027) |

**GitOps implications**: Secrets can't be stored in plain Git. Flux has native SOPS decryption support — encrypt secrets in-repo with age or GPG keys, and Flux decrypts them at apply time. Alternatively: Sealed Secrets or External Secrets Operator. The Keycloak realm JSON is especially complex — it's a ~1,100-line template with ~40 variable substitutions spanning OAuth clients, IdP configs, role definitions, and auth flows.

### 1.2 ConfigMaps

| Role | ConfigMap Name | Contents | Notes |
|------|---------------|----------|-------|
| **extractor** | `s3-env` | S3 endpoint URL, region, bucket, path-style flag | Environment config |
| **extractor** | `modality-mapping` | CSV file content (read from local path) | Maps service codes to imaging modalities |
| **extractor** | Spark defaults ConfigMap | `spark-defaults.conf` (templated) | S3/Delta/Hive config for transformer |
| **grafana** | Per-dashboard ConfigMaps | Base64-encoded dashboard JSON (templated) | 10+ dashboards, each a ConfigMap |
| **grafana** | Per-alert ConfigMaps | Alert rule JSON (templated) | 13 alert definitions, labeled `grafana_alert=1` |
| **grafana** | `notification-policy` | Alert routing rules (templated) | Routes alerts to contact points |
| **grafana** | Plugin config ConfigMap | Plugin download URLs for air-gapped | Air-gapped only |
| **jupyter** | `scout-logo` | Binary PNG data | Custom branding |
| **jupyter** | `jupyterlab-custom-css` | `custom.css` file | UI customization |
| **jupyter** | `jupyterhub-custom-templates` | `spawn.html` (templated) | Custom spawner page |
| **jupyter** | `scout-quickstart-samples` | Quickstart.ipynb + environment.yml | Sample notebooks auto-distributed to users |
| **jupyter** | Spark defaults ConfigMap | `spark-defaults.conf` (templated) | Same template as extractor, different namespace |
| **minio** | Per-policy JSON ConfigMaps | IAM policy documents | One per MinIO policy (lake-reader, lake-writer, loki-writer) |
| **oauth2-proxy** | `oauth2-proxy-templates` | Custom HTML pages (sign_in.html, error.html) | Custom auth UI |
| **superset** | `dashboard-config` | All analytics YAML files | **Superseded**: now owned by the `helm/scout-dashboards` chart (`.Files.Glob` bundling + Helm-hooked import Job); the role only cleans up the legacy unmanaged ConfigMap |
| **superset** | `scout-logo` | PNG + favicon binary data | Branding |
| **superset** | `superset-statsd-mapping` | StatsD metric mapping rules | Prometheus integration |
| **voila** | Per-playbook ConfigMaps | All files from each playbook directory | Dynamically discovered from filesystem |

**GitOps implications**: Most of these are straightforward manifests. The dynamic discovery patterns (Grafana dashboards, Voilà playbooks, Superset analytics YAML) need a build step or Kustomize generator to replicate the `ansible.builtin.find` → ConfigMap creation loop.

### 1.3 CRD Instances (Operator-Managed Resources)

| Role | Resource | API Group | Notes |
|------|----------|-----------|-------|
| **cassandra** | `CassandraDatacenter` | `cassandra.datastax.com/v1beta1` | Cluster config, storage, heap, rack layout |
| **elasticsearch** | `Elasticsearch` | `elasticsearch.k8s.elastic.co/v1` | Node sets, storage, heap, version |
| **keycloak** | `Keycloak` CR | `k8s.keycloak.org/v2alpha1` | HTTP settings, hostname, proxy headers, additional options |
| **keycloak** | `Keycloak` + `KeycloakRealmImport` CRDs | `k8s.keycloak.org` | Applied from upstream URLs before operator deploy |
| **postgres** | `Cluster` | `postgresql.cnpg.io/v1` | Instances, storage, parameters, superuser, monitoring |
| **traefik** | `TLSStore` | `traefik.io/v1alpha1` | Default certificate reference |
| **traefik** | `HelmChartConfig` | `helm.cattle.io/v1` | K3s-specific Traefik reconfiguration (TLS, telemetry) |

**GitOps implications**: Standard K8s manifests — GitOps handles these well. The Keycloak CRDs are currently fetched from upstream URLs at deploy time — these need to be vendored or version-pinned in the repo. CRD instances that depend on their operator being ready need ordering: in Argo, sync waves (operator at wave 0, CR at wave 1); in Flux, separate `Kustomization` with `dependsOn` on the operator.

### 1.4 Traefik Custom Resources (Middleware + Ingress)

| Role | Resource | Type | Purpose |
|------|----------|------|---------|
| **traefik** | `security-headers` | Middleware | HSTS, CSP, X-Frame-Options, Permissions-Policy |
| **traefik** | `redirect-to-launchpad` | Middleware | Catch-all redirect for unknown subdomains |
| **traefik** | Catch-all Ingress | Ingress | `*.{hostname}` with priority 1, applies auth + security chain |
| **oauth2-proxy** | `oauth2-proxy-auth` | Middleware | ForwardAuth to OAuth2-Proxy for authentication |
| **oauth2-proxy** | `oauth2-proxy-error` | Middleware | Custom error pages for 401 responses |
| **open-webui** | CSP middleware | Middleware | Data exfiltration prevention for LLM responses |
| **keycloak** | Keycloak Ingress | Ingress | Subdomain-based routing for auth endpoint |

**GitOps implications**: Straightforward manifests. The security-headers middleware has a complex CSP policy with dynamic URLs (`keycloak_base_url`, `oauth2_proxy_base_url`) that must be templated.

### 1.5 Services

| Role | Service Name | Purpose |
|------|-------------|---------|
| **postgres** | `postgres-metrics` | ClusterIP service exposing PostgreSQL metrics to Prometheus |
| **superset** | `superset-statsd-metrics` | ClusterIP service for StatsD exporter scraping |

### 1.6 PersistentVolumeClaims

| Role | PVC Name | Purpose |
|------|---------|---------|
| **orthanc** | `orthanc-data` | DICOM image storage |
| **dcm4chee** | `dcm4chee-wildfly-pvc` | Application server data |
| **dcm4chee** | `dcm4chee-storage-pvc` | DICOM storage |
| **dcm4chee** | `dcm4chee-db-pvc` | PostgreSQL data |
| **dcm4chee** | `dcm4chee-openldap-pvc` | LDAP data |
| **dcm4chee** | `dcm4chee-slapd-pvc` | LDAP config |

### 1.7 ServiceAccounts (AWS IRSA)

| Role | SA Name | Namespace | Purpose |
|------|---------|-----------|---------|
| **hive** | lake-writer SA | data namespace | S3 write access via IAM role |
| **trino** | lake-reader SA | analytics namespace | S3 read access via IAM role |
| **loki** | loki-writer SA | monitoring namespace | S3 write access for log storage |
| **jupyter** | lake-reader SA | analytics namespace | S3 read access for notebooks |

**GitOps implications**: Only created when `aws_deployment: true`. IRSA annotations (`eks.amazonaws.com/role-arn`) must be populated per-environment — via Argo ApplicationSet parameters, Flux `postBuild.substituteFrom`, or Kustomize overlays.

---

## 2. Kubernetes Jobs (Imperative Operations)

These are one-shot `batch/v1` Jobs that run imperative logic. They're the hardest category to translate to GitOps because they represent *procedures*, not *desired state*.

### 2.1 Database Initialization Jobs

**Pattern**: `scout_common/execute_sql.yaml` creates a `postgres:16` Job that pipes SQL via `psql`.

| Caller | Job Purpose | SQL Operations |
|--------|------------|----------------|
| **hive** | Create hive DB + owner | `CREATE USER`, `CREATE DATABASE` |
| **hive** | Create readonly user | `CREATE USER`, `GRANT CONNECT/USAGE/SELECT`, `ALTER DEFAULT PRIVILEGES` |
| **extractor** | Create ingest DB + owner | `CREATE USER`, `CREATE DATABASE` |
| **keycloak** | Create keycloak DB + owner | `CREATE USER`, `CREATE DATABASE` |
| **superset** | Create superset DB + owner | `CREATE USER`, `CREATE DATABASE` |
| **open-webui** | Create open-webui DB + owner | `CREATE USER`, `CREATE DATABASE` |
| **open-webui** | Alter schema ownership | `ALTER SCHEMA public OWNER TO` |

**Can absorb into existing mechanism?** Partially. CloudNativePG's Cluster CRD supports `bootstrap.initdb.postInitApplicationSQL` — Scout doesn't use this today but could put all `CREATE USER`/`CREATE DATABASE`/`GRANT` statements there. **Caveat**: `postInitApplicationSQL` only runs on initial cluster creation. If a feature is enabled later (e.g., `enable_chat` adds the open-webui database), the bootstrap won't re-run. This works for databases known at deploy time but not for late additions.

**GitOps approach**: For the initial set, use `postInitApplicationSQL` in the Cluster CRD. For late-addition databases, keep a standalone Job manifest that runs after postgres is healthy — in Argo, annotate with `argocd.argoproj.io/hook: PostSync`; in Flux, put in a separate `Kustomization` with `dependsOn` on the postgres resource. The Job can use `CREATE DATABASE IF NOT EXISTS`-equivalent PL/pgSQL to stay idempotent.

### 2.2 MinIO IAM Bootstrap Job

**Role**: `minio`
**What it does**: Creates a `batch/v1` Job that:
1. Uses the MinIO Client (`mc`) image
2. Sources root credentials
3. Creates IAM policies from JSON ConfigMaps
4. Attaches policies to users
5. Detaches default `consoleAdmin` from non-root users

**Can absorb into existing mechanism?** No. The MinIO Tenant CRD handles user creation but not IAM policies. The upstream chart has no post-install hook for running `mc` commands.

**GitOps approach**: Standalone Job manifest. In Argo, annotate as a PostSync hook on the MinIO Application. In Flux, put in its own `Kustomization` with `dependsOn` on the MinIO tenant `HelmRelease`. Either way, the Job needs a readiness check (init container or retry loop) to wait for MinIO to be accepting connections before running `mc` commands.

### 2.3 Temporal Scheduled Task Setup Job

**Role**: `temporal`
**What it does**: Creates a Job using `temporalio/admin-tools` that runs:
```
temporal schedule create/update --schedule-id ScheduledReportIngest \
  --cron '...' --task-queue ingest-hl7-log --type IngestHl7LogWorkflow
```

**Can absorb into existing mechanism?** Not cleanly. The upstream Temporal chart supports `server.extraInitContainers`, but init containers run *before* the server starts — the schedule creation needs a *running* Temporal server. Putting it as an init container on the worker pod would require a retry loop waiting for the server, and it would re-run on every pod restart.

**GitOps approach**: Standalone Job manifest. In Argo, annotate as a PostSync hook. In Flux, put in its own `Kustomization` with `dependsOn` on the Temporal `HelmRelease`. The Job already uses `temporalio/admin-tools` which has the CLI built in — add a readiness poll at the start of the script.

### 2.4 Keycloak Realm Configuration

**Role**: `keycloak` → `configure.yaml`
**What it does**: Deploys `keycloak-config-cli` (a Scout-owned Helm chart at `helm/keycloak-config-cli/`) as a Job that imports the ~1,100-line realm JSON.

**Can absorb into existing mechanism?** Already absorbed. The chart uses standard Helm hooks (`helm.sh/hook: post-install,post-upgrade,post-rollback`). Both Argo and Flux respect Helm hooks natively — no changes needed.

**GitOps approach**: Deploy as a HelmRelease (Flux) or Application (Argo) with a dependency on the Keycloak resource. No changes needed to the chart.

### 2.5 Grafana Plugin Staging Job (Air-Gapped Only)

**Role**: `grafana`
**What it does**: Runs an ORAS container on the *staging cluster* that:
1. Downloads Grafana plugins from grafana.com
2. Pushes them as OCI artifacts to Harbor
3. Production Grafana pulls plugins from Harbor

**Can absorb into existing mechanism?** Already partially absorbed — Grafana's upstream chart is already using `extraInitContainers` on the production side to pull plugins from Harbor. The staging-side Job is a separate concern.

**GitOps approach**: This is a staging-cluster operation. It belongs in the staging cluster's GitOps configuration as a post-install Job (Argo PostSync hook or Flux `Kustomization` with `dependsOn` on Harbor). The production Grafana deployment doesn't need changes — it already uses init containers for plugin loading.

### 2.6 Ollama Model Pull Job

**Role**: `open-webui`
**What it does**: Creates a Job that:
- **Air-gapped**: Runs on staging cluster, pulls models to shared NFS
- **Online**: Runs on production cluster, pulls models to local Ollama
- Uses `ollama pull` for each model, optionally `ollama create` for custom Scout model

**Can absorb into existing mechanism?** No. Model provisioning is inherently imperative and takes significant time/bandwidth.

**GitOps approach**: Keep as a standalone Job manifest ordered after the Open WebUI deployment. For air-gapped, it runs on the staging cluster after Ollama is healthy. For online, it runs on the production cluster after the Open WebUI deployment.

### 2.7 DICOM Population Jobs

**Roles**: `orthanc`, `dcm4chee`
**What they do**: Run `dcm4che-tools` containers that `storescu` DICOM images into the PACS server from a hostPath mount.

**Can absorb?** No, and doesn't need to — test data loading stays as a manual operation.

### 2.8 Summary: Imperative Job Migration Strategy

| Job | Can Absorb? | Argo Mechanism | Flux Mechanism |
|-----|-------------|----------------|----------------|
| DB init (known at deploy) | Yes — CNPG `postInitApplicationSQL` | Part of Cluster CRD manifest | Part of Cluster CRD manifest |
| DB init (late additions) | No | PostSync hook Job | Job Kustomization + `dependsOn` postgres |
| MinIO IAM bootstrap | No | PostSync hook Job | Job Kustomization + `dependsOn` MinIO |
| Temporal schedule | No | PostSync hook Job | Job Kustomization + `dependsOn` Temporal |
| Keycloak realm | Already a Helm hook | Helm hook (native) | Helm hook (native) |
| Grafana plugins (staging) | Partially | PostSync hook on staging | Job Kustomization on staging |
| Ollama models | No | PostSync hook Job | Job Kustomization + `dependsOn` |
| DICOM population | No | Manual operation | Manual operation |

**Net result**: ~4 standalone post-install Jobs needed (MinIO IAM, Temporal schedule, late-addition DBs, Ollama models). In Argo, these are hook annotations on the Job manifest. In Flux, each becomes its own `Kustomization` resource with a `dependsOn` edge — more CRDs to manage but more explicit.

---

## 3. Host-Level Operations (Not Kubernetes)

These operations run on the actual nodes via SSH — they cannot be expressed as Kubernetes manifests.

### 3.1 K3s Cluster Bootstrap

**Role**: `k3s`

| Operation | Target | What It Does |
|-----------|--------|-------------|
| Create data directory | server, agents | `file` module creates base K3s path |
| Run K3s install script | server | Executes `install.sh` with env vars (token, taint flags, data-dir, etc.) |
| Run K3s agent install | agents | Joins worker nodes to cluster |
| Configure kubeconfig | server → localhost | Slurps kubeconfig, rewrites server address, copies locally |
| Wait for API server | server | Polls port 6443 |
| Wait for node Ready | server | `kubectl get nodes` loop until all nodes are Ready |
| Apply control-plane taint | server | `PreferNoSchedule` on control plane (multi-node only) |
| Restart k3s service | server, agents | Triggered by registry.yaml config changes |

**Air-gapped binary preparation** (runs on staging → copies to nodes):
- Downloads k3s binary and install script
- Copies to target nodes
- Sets `INSTALL_K3S_SKIP_DOWNLOAD=true`

**GitOps implications**: K3s installation is pure infrastructure bootstrapping. It must happen before any GitOps agent can run. This stays as Ansible, Terraform, or a cloud-init script.

### 3.2 K3s Registry Configuration (Air-Gapped)

**Role**: `k3s` → `registry.yaml`

Writes `/etc/rancher/k3s/registries.yaml` on every node with:
- Mirror configuration pointing all upstream registries (docker.io, ghcr.io, quay.io, etc.) to Harbor pull-through proxies
- TLS CA certificate for staging node's self-signed cert
- Restarts k3s/k3s-agent service when config changes

**GitOps implications**: Node-level file. Must be managed by the K3s bootstrapping process, not GitOps.

### 3.3 CoreDNS Custom Configuration

**Role**: `k3s` → `coredns.yaml`

Manages a `coredns-custom` ConfigMap in kube-system that adds custom DNS forward zones. Handles:
- Forward domain resolution to specific DNS servers
- Air-gapped deny-all block for external resolution
- Restarts CoreDNS deployment when config changes

**GitOps implications**: The ConfigMap itself is a standard manifest. The rollout restart needs a config hash annotation pattern (common in GitOps).

### 3.4 GPU Node Configuration

**Role**: `k3s` → `gpu.yaml`

On GPU worker nodes:
- Writes `/etc/rancher/k3s/config.yaml` with `default-runtime: nvidia`
- Installs NVIDIA container toolkit packages via `ensure_packages`

**GitOps implications**: Node-level package installation and K3s runtime config. Stays as bootstrapping.

### 3.5 Staging CA Trust Distribution

**Role**: `scout_common` → `configure_staging_ca.yaml`

On all cluster nodes:
- Copies staging node's self-signed CA cert to `/etc/pki/ca-trust/source/anchors/staging-ca.crt`
- Runs `update-ca-trust`

**GitOps implications**: System trust store updates. Must happen at node level before pods can trust the staging registry.

### 3.6 RPM Package Installation

**Role**: `scout_common` → `ensure_packages.yaml`

Installs RPM packages on nodes with online/proxy/air-gapped awareness:
- `python3-kubernetes` (required for Ansible K8s modules)
- NVIDIA toolkit packages (GPU nodes)
- Handles yum repo configuration for Nexus proxy mode

**GitOps implications**: Node provisioning. Stays as infrastructure automation.

### 3.7 Helm Binary Installation

**Role**: `helm`

Downloads and installs the Helm binary on the server node (or localhost for air-gapped). Installs `helm-diff` plugin.

**GitOps implications**: Not needed — both Argo and Flux handle Helm chart installation natively.

### 3.8 Squid Proxy (Air-Gapped)

**Role**: `squid`

On the staging node:
- Installs `squid` system package
- Templates `/etc/squid/squid.conf` with domain allowlist (derived from configured IdPs)
- Enables and starts systemd service

**GitOps implications**: System-level service on a non-K8s node. Stays as infrastructure automation or moves to a containerized proxy.

### 3.9 Staging Node TLS Certificate Generation

**Playbook**: `staging.yaml`

Pre-tasks generate a self-signed TLS certificate if none provided:
- `community.crypto.openssl_privatekey`
- `community.crypto.x509_certificate`

**GitOps implications**: One-time infrastructure bootstrapping.

---

## 4. External API Interactions

These are operations that call non-Kubernetes APIs to configure external systems.

### 4.1 Harbor Registry Setup (Air-Gapped)

**Role**: `harbor`

After Helm install, makes Harbor API calls to:
1. Wait for Harbor health endpoint
2. Create registry endpoints (proxy caches for docker.io, ghcr.io, quay.io, etc.)
3. Create proxy cache projects linked to those registries

**Grafana plugin staging** also interacts with Harbor API:
1. Creates `grafana-plugins` project
2. Manages robot accounts (create/delete for sync)
3. Stores robot credentials as K8s Secrets

**GitOps implications**: Harbor doesn't have a K8s CRD for registry/project management. Options:
- Keep as a PostSync hook script
- Use Harbor's Terraform provider
- Build a lightweight controller

### 4.2 Nexus EULA Acceptance

**Role**: `nexus`

After Helm install:
1. Polls Nexus health endpoint
2. Checks EULA status via REST API
3. Accepts EULA via POST

**GitOps implications**: One-time post-install step. Argo CD PostSync hook.

### 4.3 Keycloak Realm Configuration

**Role**: `keycloak` → `configure.yaml`

Uses the `keycloak-config-cli` Helm chart (a Job) to import the realm JSON. The realm JSON is:
- ~1,100 lines of templated JSON
- Defines 7+ OAuth2 clients with secrets, redirect URIs, web origins
- Defines client roles (admin, user, editor, viewer per service)
- Configures identity providers (GitHub, Microsoft — conditional)
- Sets up authentication flows, client scopes, protocol mappers
- Includes SMTP configuration for email

The chart is deployed with a hash annotation that forces re-execution when the realm config changes.

**GitOps implications**: The keycloak-config-cli chart works well with GitOps — it's already a Helm release. The challenge is templating the ~1,100-line realm JSON with environment-specific values. Needs a values overlay per environment.

---

## 5. Wait/Health Check Orchestration

Ansible enforces deployment ordering through explicit waits. In GitOps, these become dependency edges and health checks.

| Role | What It Waits For | Current Method | GitOps Equivalent |
|------|-------------------|----------------|-------------------|
| **cassandra** | CassandraDatacenter Ready | `kubectl wait` (600s) | Health check on CRD status |
| **elasticsearch** | Elasticsearch health green/yellow | `k8s_info` poll (60×10s) | Health check on CRD status |
| **hive** | Deployment Available | `kubectl wait` (300s) | Built-in Deployment readiness |
| **keycloak** | Operator Deployment ready | `k8s_info` poll (300s) | Ordering: operator before CR |
| **keycloak** | Keycloak CR Ready | `k8s_info` poll (300s) | Health check on CRD status |
| **minio** | StatefulSet exists + ready | `k8s_info` poll (60×5s + 60×5s) | Built-in StatefulSet readiness |
| **postgres** | Cluster Ready | `kubectl wait` (300s) | Health check on CRD status |
| **traefik** | helm-install-traefik job complete | `kubectl wait` | Dependency ordering |
| **valkey** | Deployment Available | `k8s_info` poll (300s) | Built-in Deployment readiness |
| **open-webui** | Pod Ready | `kubectl wait` | Built-in Deployment readiness |
| **harbor** | Health endpoint 200 | `uri` poll (30×10s) | Pod readiness + retry logic in dependent Jobs |
| **nexus** | Health endpoint 200 | `uri` poll (30×10s) | Pod readiness + retry logic in dependent Jobs |

**Argo approach**: Custom lua-based health checks can be defined per CRD type. This handles non-standard status fields (e.g., if CassandraDatacenter doesn't use standard Ready conditions). Sync waves provide implicit ordering.

**Flux approach**: `Kustomization.spec.healthChecks` can target specific resources by apiVersion/kind/name/namespace, but relies on standard `.status.conditions[type=Ready]`. CloudNativePG and Keycloak follow this convention. For CRDs that don't (CassandraDatacenter, Elasticsearch), `Kustomization.spec.healthCheckExprs` (CEL expressions, Flux ≥2.5) expresses custom readiness against arbitrary status fields — the fallback to `dependsOn` with generous timeouts this paragraph originally recommended is no longer necessary.

**Both tools**: For HTTP health checks (Harbor, Nexus), neither has a built-in "wait for HTTP 200" mechanism. Pod readiness doesn't guarantee API availability. Post-install Jobs that depend on API readiness should include their own retry logic in the Job script.

---

## 6. Deployment Ordering Dependencies

The playbook import order in `main.yaml` encodes critical dependencies:

```
Layer 0 (Bootstrap — not GitOps):
  scout.yaml → staging.yaml → k3s.yaml → traefik.yaml → helm.yaml → gpu.yaml

Layer 1 (Foundation — sync wave 0):
  postgres.yaml → valkey.yaml

Layer 2 (Auth — sync wave 1, depends on postgres + valkey):
  auth.yaml (keycloak + oauth2-proxy)

Layer 3 (Data — sync wave 2, depends on postgres + auth):
  lake.yaml (minio + hive)

Layer 4 (Applications — sync wave 3, depends on all above):
  chatbot.yaml, voila.yaml, analytics.yaml (trino + superset)

Layer 5 (Processing — sync wave 4, depends on lake + temporal infra):
  orchestrator.yaml (cassandra + elasticsearch + temporal)
  extractor.yaml

Layer 6 (User Services — sync wave 5):
  jupyter.yaml

Layer 7 (Observability — sync wave 6, scrapes everything):
  monitor.yaml (prometheus + loki + grafana)

Layer 8 (Portal — sync wave 7, links to everything):
  launchpad.yaml
```

**Argo approach**: Each layer maps to a sync wave annotation (`argocd.argoproj.io/sync-wave: "1"`). Resources within the same wave sync in parallel; waves execute sequentially. The App of Apps pattern wraps each layer as a child Application.

**Flux approach**: Each layer maps to one or more `Kustomization` or `HelmRelease` resources with explicit `dependsOn` edges. Dependencies form a named DAG — more verbose but also more explicit. Cross-namespace dependencies require `spec.dependsOn[].namespace`.

```yaml
# Example: Flux dependency ordering
apiVersion: helm.toolkit.fluxcd.io/v2        │  # Example: Argo sync wave ordering
kind: HelmRelease                             │  apiVersion: argoproj.io/v1alpha1
metadata:                                     │  kind: Application
  name: keycloak                              │  metadata:
spec:                                         │    name: keycloak
  dependsOn:                                  │    annotations:
    - name: postgres                          │      argocd.argoproj.io/sync-wave: "2"
    - name: valkey                            │  spec: ...
```

---

## 7. Dynamic/Computed Configuration

Ansible computes values at deploy time that are not static. These require equivalent computation in a GitOps pipeline.

### 7.1 Conditional Deployments

| Condition | What Changes |
|-----------|-------------|
| `air_gapped: true` | Enables staging node, Harbor mirrors, Nexus proxy, staging CA trust, registry config, CoreDNS deny-all |
| `aws_deployment: true` | Skips MinIO, uses IRSA ServiceAccounts, changes S3 credentials provider, disables path-style access |
| `enable_chat: true` | Deploys Open WebUI + Ollama + CSP middleware + model pull jobs |
| `enable_playbooks: true` | Deploys Voilà with dynamically-discovered playbook ConfigMaps |
| `enable_xnat: true` | Deploys XNAT (upstream OCI chart), its Keycloak client + realm role, prefs-init Secret, plugin installer (ADRs 0026/0027) |
| `enable_data_generator: true` | Deploys the synthetic HL7 data generator |
| `gpu_workers` group non-empty | Deploys GPU Operator, configures NVIDIA runtime, enables GPU JupyterHub profiles |
| `package_proxy_mode == 'nexus'` | Deploys Nexus on staging, configures yum repos, trusts staging CA |
| IdP-specific vars defined | Adds GitHub/Microsoft identity providers to Keycloak realm |
| `alert_contact_point` defined | Creates Grafana alert secrets and notification policies |

**Argo approach**: ApplicationSets with conditional generators — a matrix generator can produce Applications for each enabled feature, or a Git generator can target different directories per environment variant.

**Flux approach**: Two patterns:
- **Kustomize overlays**: A `base/` with all components, and overlays like `overlays/air-gapped/`, `overlays/aws/`, `overlays/with-chat/` that add or patch resources. Compose overlays for combinations.
- **`postBuild` variable substitution**: Set feature flags in a ConfigMap, reference them with `${enable_chat}` in manifests.

**Both tools**: For Scout's matrix of variants (air-gapped × AWS × chat × playbooks × GPU), Kustomize overlays are the most maintainable pattern regardless of tool choice — they work identically in both Argo and Flux.

### 7.2 Template Variable Computation

Key computed values in `scout_common/defaults/main.yaml`:

```yaml
# Computed from inventory
server_hostname: "{{ hostvars[groups['server'][0]].ansible_host }}"
db_host: "{{ postgres_cluster_name }}-rw.{{ postgres_namespace }}"
db_host_ro: "{{ postgres_cluster_name }}-ro.{{ postgres_namespace }}"
s3_endpoint: "{{ 'https://minio...' if not aws_deployment else 'https://s3...' }}"
keycloak_base_url: "https://keycloak.{{ external_url | default(server_hostname) }}"
# ... ~30 more derived URLs

# JWT/OIDC endpoints (all derived from keycloak_base_url)
keycloak_oidc_auth_url, keycloak_oidc_token_url, keycloak_oidc_userinfo_url,
keycloak_oidc_certs_url, keycloak_oidc_logout_url, keycloak_oidc_well_known_url
```

**GitOps approach**: A values generation step is the most realistic path for both tools. A CI step or Makefile target takes an environment config file (replacing `inventory.yaml`), computes all derived values (the ~30 URLs, memory conversions, etc.), and writes out Helm values files and/or Kustomize patches. Commit the output and the GitOps controller syncs it.

Flux's `postBuild.substituteFrom` can handle simple substitutions (`${external_url}`) but not chained derivations (`keycloak_base_url` → `keycloak_oidc_auth_url`). Argo has no equivalent — it relies on Helm templating or ApplicationSet template parameters. For Scout's complexity, pre-computation is cleaner than either tool's runtime substitution.

### 7.3 Custom Jinja2 Filters

`filter_plugins/jvm_memory.py` provides:
- `jvm_memory_to_k8s(heap, multiplier)`: Converts `"2G"` → `"2Gi"`, `"2G" * 2` → `"4Gi"`
- `multiply_memory(memory, multiplier)`: `"8G" * 2` → `"16G"`

Used by: Cassandra, Elasticsearch, Trino, HL7 Transformer, JupyterHub resource calculations.

**GitOps implications**: These conversions must be done either:
- At values-generation time (script that writes Helm values)
- In Helm templates (custom template functions)
- Manually pre-computed in values files

---

## 8. File Discovery and Bundling

Several roles dynamically discover files from the local filesystem and bundle them into ConfigMaps.

| Role | Discovery Pattern | Result |
|------|-------------------|--------|
| **grafana** | `find` all `*.json.j2` in `templates/dashboards/` | One ConfigMap per dashboard |
| **grafana** | `find` all `*.json.j2` in `templates/alerts/` | One ConfigMap per alert rule |
| **grafana** | `find` all `*.json.j2` in `templates/contact-points/` | One Secret per contact point |
| **superset** | ~~`find` all `*.yaml` / `*.yaml.j2` in analytics directories~~ | **Done**: moved into `helm/scout-dashboards` (`.Files.Glob` + Helm-hooked import Job) — the approach recommended below, since shipped |
| **oauth2-proxy** | `find` all `*.html` in `files/` | Single `oauth2-proxy-templates` ConfigMap |
| **jupyter** | `find` all files in `files/samples/` | Single `scout-quickstart-samples` ConfigMap |
| **voila** | `find` directories + files recursively | One ConfigMap per playbook directory |
| **extractor** | `stat` + `lookup` for modality mapping CSV | Single `modality-mapping` ConfigMap |

**GitOps approach**: Kustomize `configMapGenerator` is the natural fit — both Argo and Flux apply Kustomize natively:
```yaml
# kustomization.yaml
configMapGenerator:
  - name: scout-quickstart-samples
    files:
      - samples/Quickstart.ipynb
      - samples/environment.yml
```
For the dynamic discovery patterns (Grafana dashboards, Voilà playbooks), you'd either:
- Enumerate files explicitly in the `configMapGenerator` (loses auto-discovery but gains determinism)
- Use a CI pre-generation step that discovers files and writes the `kustomization.yaml`
- For Scout-owned Helm charts, use Helm's `{{ .Files.Glob }}` to bundle files from the chart directory

---

## 9. Cross-Cluster Operations (Air-Gapped)

Several operations span the staging and production clusters:

| Operation | Source | Target | What Happens |
|-----------|--------|--------|-------------|
| Registry mirrors | Harbor (staging) | K3s nodes (prod) | `registries.yaml` written to all nodes |
| CA certificate | Staging node | All prod nodes + K8s Secrets | Cert slurped and distributed |
| Grafana plugins | grafana.com → Harbor (staging) | Grafana pods (prod) | OCI artifacts pushed to Harbor, pulled by Grafana |
| Ollama models | ollama.ai → NFS (staging) | Ollama pods (prod) | Models pulled to shared NFS, mounted read-only |
| Package proxy | Internet → Nexus (staging) | JupyterHub pods (prod) | Conda/pip/yum proxied through Nexus |
| IdP auth | External IdPs → Squid (staging) | Keycloak pods (prod) | OAuth redirects proxied through Squid |

**GitOps approach**: Both tools support multi-cluster. Flux runs an instance per cluster pointing at different paths in the same repo (e.g., `clusters/staging/`, `clusters/production/`). Argo can manage remote clusters from a central instance via ApplicationSets.

Cross-cluster dependencies (staging Harbor must be populated before production Grafana deploys) can't be expressed as in-tool dependencies across cluster boundaries in either tool. Options:
- Accept that staging is provisioned first (it's infrastructure bootstrapping) and production GitOps assumes it's ready
- Use a CI pipeline that provisions staging, verifies readiness, then triggers production sync
- For the CA cert distribution specifically, this stays as node-level Ansible — it's not a K8s resource

---

## 10. Summary: What Stays vs. What Moves

### Stays as Infrastructure Automation (Ansible/Terraform/Cloud-Init)
- K3s cluster installation and node configuration
- GPU driver and runtime setup
- Registry mirror configuration on nodes (`/etc/rancher/k3s/registries.yaml`)
- Staging CA trust store updates (`update-ca-trust`)
- RPM package installation (NVIDIA toolkit, python3-kubernetes)
- Squid proxy on staging node (system package + systemd)
- Staging node TLS certificate generation
- K3s binary preparation (air-gapped)
- Flux bootstrap itself (initial `flux bootstrap` command)

### Moves to GitOps-Managed Helm Releases
- All upstream Helm chart releases (MinIO, Temporal, Superset, Grafana, Prometheus, Loki, JupyterHub, OAuth2-Proxy, Valkey, Harbor, Nexus, GPU Operator, XNAT — already an upstream OCI chart, etc.)
- Scout-owned Helm charts (hive-metastore, hl7log-extractor, hl7-transformer, keycloak-config-cli, voila, scout-opa, scout-dashboards, launchpad) — published as versioned OCI artifacts per ADR 0030, pinned by version rather than sourced from a git branch
- Each with environment-specific values referencing ConfigMaps/Secrets
- In Argo: `Application` per chart. In Flux: `HelmRelease` per chart.

### Moves to GitOps-Managed Raw Manifests
- CRD instances (CassandraDatacenter, Elasticsearch, Keycloak CR, CloudNativePG Cluster)
- Traefik Middleware and Ingress resources
- All Secrets (via SOPS, Sealed Secrets, or External Secrets Operator)
- ConfigMaps built via Kustomize `configMapGenerator`
- ServiceAccount definitions (IRSA)
- PVC definitions
- Service definitions (postgres-metrics, superset-statsd-metrics)
- Namespace definitions

### Moves to Post-Install Jobs (Ordered After Parent)
- MinIO IAM bootstrap (after MinIO tenant ready)
- Temporal schedule creation (after Temporal server ready)
- Late-addition database initialization (after postgres ready)
- Ollama model pull (after Open WebUI / Ollama ready)
- Harbor registry/project creation (after Harbor ready)
- Nexus EULA acceptance (after Nexus ready)
- In Argo: annotate with `argocd.argoproj.io/hook: PostSync`. In Flux: separate `Kustomization` with `dependsOn`.

### Needs a Build/Generation Step (CI or Makefile)
- Grafana dashboard/alert ConfigMaps from Jinja2 templates → pre-render to JSON, reference via `configMapGenerator`
- Keycloak realm JSON from ~1,100-line template → pre-render per environment, store as SOPS-encrypted Secret
- Superset dashboard-config ConfigMap from discovered files → enumerate in `configMapGenerator`
- Voilà playbook ConfigMaps from discovered directories → enumerate in `configMapGenerator` or use Helm `.Files.Glob`
- Spark defaults ConfigMap from template → pre-render per environment
- JVM-to-K8s memory conversions → pre-compute in values generation script
- All ~30 computed/derived URLs and endpoints → pre-compute from base variables

### Already Works As-Is with Either Tool
- Keycloak realm import (keycloak-config-cli chart uses Helm hooks — both Argo and Flux respect these)
- Superset dashboard import (`scout-dashboards` chart: Helm-hooked import Job, same pattern)
- Grafana init containers for air-gapped plugin loading (already in upstream chart values)
- JupyterHub init containers for Spark config and CA cert (already in upstream chart values)

---

## 11. Argo CD vs Flux CD Considerations for Scout

> **Decided (ADR 0031, 2026-07): Flux.** The comparison below is retained as
> the analysis of record.

### Where Flux is stronger for Scout

**Native SOPS decryption.** Scout has 50+ Secrets across roles. Flux decrypts SOPS-encrypted files inline during reconciliation — no additional operator needed. With Argo CD, you'd need `ksops` (a Kustomize plugin) or External Secrets Operator. Given the volume of secrets and the air-gapped deployment constraint (which complicates external secret stores), Flux's built-in SOPS is a significant operational simplification.

**`postBuild` variable substitution.** Scout's ~30 derived URLs and per-environment configuration values can be injected into manifests via `postBuild.substituteFrom`, referencing a ConfigMap of environment variables. This is simpler than Argo's approach of wrapping everything in Helm or using ApplicationSet template parameters.

**Lighter operational footprint.** Flux runs as a set of controllers (source-controller, kustomize-controller, helm-controller). No server process, no Redis, no repo-server, no UI server. For on-prem/air-gapped deployments where every component adds operational surface, this matters.

**Multi-cluster is a first-class pattern.** Flux's model of "one repo, different paths per cluster" maps cleanly to Scout's staging ↔ production topology. Each cluster runs its own Flux instance pointing at `clusters/staging/` or `clusters/production/`.

**Helm hook support.** Flux respects standard Helm hooks (`helm.sh/hook`) in `HelmRelease` resources. The keycloak-config-cli chart's `post-install,post-upgrade,post-rollback` hooks work without modification.

**`dependsOn` is more explicit than sync waves.** Scout's 8-layer dependency graph becomes a named DAG of `HelmRelease` and `Kustomization` resources. Each dependency is a specific resource reference, not an integer annotation. Easier to reason about in code review and debug when something fails.

### Where Argo CD is stronger for Scout

**Native hook system for imperative Jobs.** Scout has ~6 post-install Jobs (MinIO IAM, Temporal schedule, Harbor API, Nexus EULA, Ollama models, late DB init). In Argo, each is a manifest with a `argocd.argoproj.io/hook: PostSync` annotation — the Job runs after its parent Application syncs, and Argo manages the lifecycle (cleanup, retry, timeout). In Flux, each becomes its own `Kustomization` resource with `dependsOn`, health checks, and manual lifecycle management. The Flux approach works but requires ~6 additional Flux CRDs and more careful wiring.

**Custom health checks for operator CRDs.** Argo supports lua-based health checks — you can write custom logic to determine if a CassandraDatacenter, Elasticsearch CR, or CloudNativePG Cluster is truly healthy. Flux's `healthChecks` rely on standard `.status.conditions[type=Ready]` semantics. Most of Scout's operator CRDs follow this convention, but if any don't (e.g., CassandraDatacenter uses a custom status field), Flux can't express "wait until this non-standard condition is met." *(Update, 2026-07: no longer true — Flux ≥2.5 adds CEL-based `healthCheckExprs` for exactly this; the advantage has closed.)*

**UI for operational visibility.** Scout is a 20+ service platform. Argo's web UI shows sync status, health, diff, and logs for every Application in one view. Flux has no built-in UI — you'd rely on `flux get` CLI commands, the Flux Grafana dashboards, or a third-party UI like Weave GitOps. For operators who aren't CLI-fluent, this is a gap.

**ApplicationSets for conditional deployments.** Scout's feature flag matrix (air-gapped × AWS × chat × playbooks × GPU) maps naturally to Argo's ApplicationSet generators with conditional logic. In Flux, this maps to Kustomize overlays — workable, but the overlay composition for combinations (air-gapped + chat + GPU) can get unwieldy.

### Neutral — works equally well in both

- Helm chart management (both have first-class HelmRelease/Application support)
- Git-based source of truth
- Automated reconciliation and drift detection
- Namespace management
- RBAC and multi-tenancy
- Integration with CI/CD pipelines
- Kustomize support

### Recommendation

Either tool can handle Scout's deployment. The deciding factors are:

1. **If the team prioritizes operational simplicity and has CLI comfort**: Flux. Fewer moving parts, native SOPS, lighter footprint, cleaner multi-cluster story. The ~6 extra `Kustomization` resources for imperative Jobs are manageable overhead.

2. **If the team prioritizes operational visibility and has many imperative post-install steps**: Argo CD. The hook system and UI reduce plumbing for the imperative Jobs, and the dashboard is valuable for a platform this complex.

3. **If air-gapped deployment is a primary concern**: Flux has an edge. Fewer components to mirror into Harbor, OCI-based source support (`OCIRepository` can pull from Harbor directly), and the staging/production cluster split maps cleanly to Flux's multi-instance model.

4. **If the team already has experience with one tool**: Use that one. The migration from Ansible is the hard part — the choice between Argo and Flux is secondary to actually restructuring the deployment into declarative manifests with a dependency graph.
