# Creating the Ansible Inventory File

This guide walks you through creating the `inventory.yaml` file required for deploying Scout using Ansible. The inventory file defines your infrastructure, including server nodes, configuration variables, and secrets needed for deployment.

## Overview

The `inventory.yaml` file is an Ansible inventory that tells Scout where to deploy services and how to configure them. It contains:

- **Host definitions**: Server, worker, and GPU nodes that form your K3s cluster
- **Connection parameters**: SSH credentials and authentication methods
- **Storage paths**: Directory locations for persistent data
- **Resource allocations**: CPU, memory, and storage sizes for services
- **Secrets**: Encrypted passwords, tokens, and credentials
- **Service configuration**: Component-specific settings and overrides

## Quick Start

1. Copy the example inventory file:
   ```bash
   cd ansible
   cp inventory.example.yaml inventory.yaml
   ```

2. Edit `inventory.yaml` to customize for your environment

3. Encrypt secrets using Ansible Vault (see {ref}`Configuring Secrets <configuring-secrets>`)

4. Deploy Scout:
   ```bash
   make all
   ```

## Infrastructure Requirements

### Minimum Setup

For testing or small deployments:
- 1 server node (control plane + worker)
- 16 CPU cores
- 64GB RAM
- 500GB storage

### Recommended Setup

For production deployments:
- 1 server node (control plane)
- 2+ worker nodes
- GPU node(s) for AI/ML workloads (optional)
- Dedicated staging node for air-gapped deployments (optional)

### Storage Recommendations

Default storage allocations (can be customized in `inventory.yaml`):
- MinIO: 750Gi (data lake storage)
- Cassandra: 300Gi (Temporal persistence)
- Elasticsearch: 100Gi (Temporal visibility)
- PostgreSQL: 100Gi (application databases)
- Prometheus: 100Gi (metrics)
- Loki: 100Gi (logs)
- Jupyter: 250Gi (user notebooks)
- Ollama: 200Gi (AI models)
- Open WebUI: 100Gi (chat interface data)

## Inventory Structure

The inventory file is organized into host groups and variables. Here's the basic structure:

```yaml
all:
  vars:
    # Global variables (SSH, authentication)

staging:
  hosts:
    # Staging node for air-gapped deployments

server:
  hosts:
    # Control plane node(s)

workers:
  hosts:
    # Worker nodes

gpu_workers:
  hosts:
    # GPU-enabled worker nodes

agents:
  children:
    workers:
    gpu_workers:

minio_hosts:
  children:
    # Nodes where MinIO will run

k3s_cluster:
  children:
    server:
    agents:
  vars:
    # Cluster-wide configuration
```

## Host Groups

### Global Settings (`all`)

Define SSH connection and privilege escalation settings that apply to all hosts:

```yaml
all:
  vars:
    ansible_user: 'your-ssh-username'
    ansible_become: true
    ansible_become_method: sudo
    ansible_become_user: root
    ansible_become_password: !vault |
          $ANSIBLE_VAULT;1.1;AES256
          ...encrypted password...
```

**Key variables:**
- `ansible_user`: SSH username for connecting to nodes
- `ansible_become`: Enable privilege escalation. Note: This should _not_ be set to `true` when running air-gapped installs as a non-privileged user on a remote host.
- `ansible_become_method`: How to escalate privileges (typically `sudo`)
- `ansible_become_password`: Encrypted sudo password

See [Ansible connection parameters](https://docs.ansible.com/ansible/latest/inventory_guide/intro_inventory.html#connecting-to-hosts-behavioral-inventory-parameters) for additional options.

### Server Group

The control plane node(s) for your K3s cluster:

```yaml
server:
  hosts:
    leader.example.edu:
      ansible_connection: local  # If running on this node
      ansible_host: leader       # SSH hostname
      ansible_python_interpreter: /usr/bin/python3
      k3s_control_node: true
      external_url: scout.example.edu  # External access URL
```

**Per-host variables:**
- `ansible_connection`: Use `local` if running Ansible on this node, omit for SSH
- `ansible_host`: Hostname for SSH connection (optional if FQDN works)
- `ansible_python_interpreter`: Path to Python interpreter on remote host
- `k3s_control_node`: Set to `true` for control plane nodes
- `external_url`: Public URL for accessing Scout services (optional, defaults to FQDN)

### Workers Group

Worker nodes that run Scout workloads:

```yaml
workers:
  hosts:
    worker-1.example.edu:
      ansible_host: worker-1
      ansible_python_interpreter: /usr/bin/python3
    worker-2.example.edu:
      ansible_host: worker-2
      ansible_python_interpreter: /usr/bin/python3
```

### GPU Workers Group

Worker nodes with NVIDIA GPUs for accelerated workloads:

```yaml
gpu_workers:
  hosts:
    gpu-1.example.edu:
      ansible_host: gpu-1
      ansible_python_interpreter: /usr/bin/python3
```

The NVIDIA GPU Operator will be automatically deployed on these nodes.

### MinIO Hosts Group

Nodes where MinIO object storage will run. MinIO requires direct disk access:

```yaml
minio_hosts:
  children:
    server:
    workers:
```

**Important:** If `minio_hosts` contains more than one node, you must set `minio_volumes_per_server` to 2 or greater in the `k3s_cluster` vars section, or MinIO will fail to start.

### Staging Group

For air-gapped deployments, define a staging node with internet access (Ansible automatically deploys K3s and Harbor on this node):

```yaml
staging:
  hosts:
    staging.example.edu:
      ansible_host: staging
      ansible_python_interpreter: /usr/bin/python3
  vars:
    staging_k3s_token: !vault |
          $ANSIBLE_VAULT;1.1;AES256
          ...encrypted token...
    harbor_admin_password: !vault |
          $ANSIBLE_VAULT;1.1;AES256
          ...encrypted password...
    harbor_storage_size: 100Gi
```

See {ref}`Air-Gapped Deployment <air-gapped-deployment>` for details.

## Cluster Configuration (`k3s_cluster` vars)

The `k3s_cluster` vars section contains the bulk of your Scout configuration:

```yaml
k3s_cluster:
  children:
    server:
    agents:
  vars:
    # Storage configuration
    # Service secrets
    # Resource allocations
    # Component-specific settings
```

### Storage Configuration

#### Storage Sizes

Define persistent volume sizes for each service:

```yaml
postgres_storage_size: 100Gi
cassandra_storage_size: 300Gi
elasticsearch_storage_size: 100Gi
jupyter_hub_storage_size: 15Gi
jupyter_singleuser_storage_size: 250Gi
prometheus_storage_size: 100Gi
loki_storage_size: 100Gi
grafana_storage_size: 50Gi
minio_storage_size: 750Gi
ollama_storage_size: 200Gi
open_webui_storage_size: 100Gi
```

#### Storage Classes

Scout uses Kubernetes dynamic volume provisioning to automatically create persistent volumes for services. There are two configuration approaches:

1. **Default (recommended for most deployments):** All services use the cluster's default storage class
2. **Multi-disk on-premise:** Configure multiple storage classes mapped to different filesystem paths for I/O isolation

##### Default Configuration (Cloud and Single-Disk On-Premise)

For cloud deployments and single-disk on-premise servers, leave all storage class variables empty to use the cluster's default storage class:

```yaml
# All per-service storage class variables empty (use cluster default)
postgres_storage_class: ""
temporal_storage_class: ""
cassandra_storage_class: ""
elasticsearch_storage_class: ""
minio_storage_class: ""
jupyterhub_storage_class: ""
jupyter_singleuser_storage_class: ""
prometheus_storage_class: ""
loki_storage_class: ""
grafana_storage_class: ""
orthanc_storage_class: ""
dcm4chee_storage_class: ""
ollama_storage_class: ""
open_webui_storage_class: ""

# No custom storage classes defined
onprem_local_path_multidisk_storage_classes: []
```

**Platform-specific default storage classes:**

- **k3s** (local development, on-premise): `local-path` (Rancher local-path-provisioner, built-in)
- **AWS EKS**: cluster default (typically `gp3`, requires EBS CSI driver addon)
- **Google GKE**: `standard-rwo` (Google Persistent Disk, HDD) or `premium-rwo` (SSD)
- **Azure AKS**: `managed-csi` (Azure Managed Disks)

##### Multi-Disk Configuration (On-Premise I/O Isolation)

For k3s on-premise deployments with multiple physical disks, you can configure custom storage classes to isolate I/O-intensive workloads across different disks:

```yaml
# Define custom storage classes (k3s on-prem multi-disk only)
onprem_local_path_multidisk_storage_classes:
  - name: "local-database"
    path: "/mnt/disk1/k3s-storage"
  - name: "local-objectstorage"
    path: "/mnt/disk2/k3s-storage"
  - name: "local-monitoring"
    path: "/mnt/disk3/k3s-storage"

# Assign services to storage classes
# Database services
postgres_storage_class: "local-database"
cassandra_storage_class: "local-database"
elasticsearch_storage_class: "local-database"

# Object storage and data processing
minio_storage_class: "local-objectstorage"
jupyterhub_storage_class: "local-objectstorage"
jupyter_singleuser_storage_class: "local-objectstorage"

# Monitoring and logging
prometheus_storage_class: "local-monitoring"
loki_storage_class: "local-monitoring"
grafana_storage_class: "local-monitoring"

# AI/ML services
ollama_storage_class: "local-objectstorage"  # Large model files
open_webui_storage_class: "local-database"   # User data and chat history

# Other services
orthanc_storage_class: "local-database"
dcm4chee_storage_class: "local-database"
temporal_storage_class: ""  # Uses Cassandra for persistence
```

**When to use multiple storage classes:**
- k3s on-premise deployment with 2+ separate physical disks
- Observing I/O contention or high iowait times
- Performance-critical databases need isolation from bulk storage operations
- Different storage tiers (NVMe for databases, HDD for bulk storage)

**Note:** This feature is k3s-specific for on-premise multi-disk deployments only. Cloud deployments and single-disk k3s installations should leave `onprem_local_path_multidisk_storage_classes` empty to use cluster defaults.

**Note:** Dynamic provisioning automatically manages node affinity for local volumes and creates storage in provisioner-managed locations.
**Note:** `extractor_data_dir` is still used for the HL7 log input directory (not managed by Kubernetes persistent volumes).

(configuring-secrets)=
### Configuring Secrets

Scout uses [Ansible Vault](https://docs.ansible.com/ansible/latest/vault_guide/index.html) to encrypt sensitive values like passwords, tokens, and API keys.

#### 1. Create a Vault Password Script

Store your vault password securely using a password manager:

```bash
mkdir -p vault
cat > vault/pwd.sh <<'EOF'
#!/bin/bash
# Retrieve vault password from your password manager
# Example using Bitwarden:
if [ -z "$BW_SESSION" ]; then
  echo "Error: BW_SESSION is not set. Please log in to Bitwarden first." >&2
  exit 1
fi
bw get password "AnsibleVault" 2>/dev/null
EOF
chmod 755 vault/pwd.sh
```

Add `vault/` to `.gitignore` to prevent committing secrets.

#### 2. Generate Encrypted Secrets

Generate and encrypt passwords using `ansible-vault encrypt_string`:

```bash
# Generate a random password
openssl rand -hex 32 | ansible-vault encrypt_string --vault-password-file vault/pwd.sh

# Encrypt an existing password from environment variable
echo $MY_PASSWORD | ansible-vault encrypt_string --vault-password-file vault/pwd.sh

# Encrypt with a label (recommended)
openssl rand -hex 32 | ansible-vault encrypt_string --vault-password-file vault/pwd.sh --name 'postgres_password'
```

#### 3. Add Encrypted Values to Inventory

Paste the encrypted output into your `inventory.yaml`:

```yaml
postgres_password: !vault |
      $ANSIBLE_VAULT;1.1;AES256
      66386439653966636331633265613234383830636161343532313361356438346533636630666364
      ...more encrypted data...
```

### Resource Allocations

Override default resource allocations for each service. All services have development-scale defaults defined in their role's `defaults/main.yaml`, but you can override them for your environment.

#### Partial Resource Overrides

Most services support **partial resource overrides**. You only need to specify the values you want to change; unspecified values will use the role defaults:

```yaml
# Override only limits (requests use defaults)
temporal_resources:
  limits:
    cpu: 4
    memory: 8Gi

# Override a single value
prometheus_resources:
  limits:
    memory: 4Gi
```

**Services supporting partial overrides:** temporal, postgres, minio, hive, prometheus, grafana, loki, superset, superset_statsd, jupyter_hub, hl7log_extractor, redis_operator, redis_cluster_node, voila, orthanc, dcm4chee, ollama, open_webui, mcp_trino

**Services NOT supporting partial overrides** (use flattened variables instead): Trino coordinator/worker, Cassandra, Elasticsearch, HL7 Transformer. These services use individual variables (e.g., `cassandra_max_heap`, `trino_worker_cpu_limit`) because JVM heap sizes drive memory calculations with different multipliers for requests vs limits.

#### PostgreSQL

```yaml
postgres_resources:
  requests:
    cpu: 4
    memory: 64Gi
  limits:
    cpu: 6
    memory: 96Gi

postgres_parameters:
  max_connections: '120'
  shared_buffers: '16GB'
  effective_cache_size: '48GB'
  maintenance_work_mem: '2GB'
  work_mem: '2GB'
```

#### Cassandra (JVM-based)

```yaml
cassandra_init_heap: 6G
cassandra_max_heap: 12G
cassandra_cpu_request: 2
cassandra_cpu_limit: 4
```

Memory is computed automatically from heap size (requests = 1x heap, limits = 2x heap).

#### Elasticsearch (JVM-based)

```yaml
elasticsearch_max_heap: 3G
elasticsearch_cpu_request: 1
elasticsearch_cpu_limit: 3
```

Memory is computed automatically from heap size (requests = 2x heap, limits = 4x heap to allow burst).

#### Trino (JVM-based)

```yaml
trino_worker_count: 2  # Number of worker replicas
trino_worker_max_heap: 8G
trino_coordinator_max_heap: 4G
trino_worker_cpu_request: 2
trino_worker_cpu_limit: 6
trino_coordinator_cpu_request: 1
trino_coordinator_cpu_limit: 3
# Optional: Override query memory allocation (default 0.3 = 30% of heap)
# trino_per_node_query_memory_fraction: 0.3

# MCP Trino server resources (used by Open WebUI for natural language SQL queries)
mcp_trino_resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 1
    memory: 2Gi
```

Memory is computed automatically from heap size (requests = 1x heap, limits = 2x heap).

**Query Memory Limits:**
- `query.max-memory-per-node` is set to `heap_size × trino_per_node_query_memory_fraction` (default 30%)
- `query.max-memory` (cluster-wide) is calculated as `worker_count × worker_heap × trino_per_node_query_memory_fraction`
- These limits scale automatically with worker count and heap size changes
- Only override `trino_per_node_query_memory_fraction` if you understand [Trino's memory management](https://trino.io/docs/current/admin/properties-resource-management.html)

**MCP Trino Server:**
The MCP Trino server is deployed as part of the Trino role when the Chat service is enabled (`enable_chat: true`). It provides an MCP (Model Context Protocol) interface to Trino for AI-powered natural language SQL queries in Open WebUI. The default resources are suitable for most deployments, but can be overridden in `inventory.yaml` if needed for high-concurrency AI query workloads.

#### MinIO

```yaml
minio_resources:
  requests:
    cpu: 2
    memory: 8Gi
  limits:
    cpu: 4
    memory: 8Gi
```

#### Loki

```yaml
loki_resources:
  requests:
    cpu: 250m
    memory: 1Gi
  limits:
    cpu: 2
    memory: 4Gi

# Memcached cache configuration (optional - has dev-friendly defaults)
# Loki uses memcached for two caching layers:
# - chunksCache: Caches log chunks to reduce S3 fetches
# - resultsCache: Caches query results for repeated queries
# Values are in MB. Pod memory is computed as allocatedMemory * 1.2
# Role defaults: 512MB chunks, 256MB results (adequate for most deployments)
# Uncomment to override for large-scale production:
# loki_chunks_cache_allocated_memory: 1024  # MB
# loki_results_cache_allocated_memory: 512  # MB
```

#### JupyterHub

```yaml
# JupyterHub profiles for user-selectable resource configurations
# Default provides "CPU Only" profile with Small/Medium/Large options
# See ansible/README.md "Customizing JupyterHub Profiles" for details

# Example: Add GPU profile alongside default CPU profile
jupyter_profiles:
  - "{{ jupyter_cpu_profile }}"  # Include default CPU profile
  - display_name: "GPU"
    slug: "gpu"
    description: "GPU environment for ML/AI workloads"
    profile_options:
      resource_allocation:
        display_name: "Resource Size"
        choices:
          medium:
            display_name: "Medium (8 CPU, 32Gi RAM, 1 GPU)"
            default: true
            kubespawner_override:
              cpu_guarantee: 4
              cpu_limit: 8
              mem_guarantee: '16G'
              mem_limit: '32G'
              environment:
                SPARK_DRIVER_MEMORY: "8g"
                SPARK_EXECUTOR_MEMORY: "8g"
              extra_resource_guarantees:
                nvidia.com/gpu: '1'
              extra_resource_limits:
                nvidia.com/gpu: '1'
          large:
            display_name: "Large (16 CPU, 64Gi RAM, 1 GPU)"
            kubespawner_override:
              cpu_guarantee: 8
              cpu_limit: 16
              mem_guarantee: '32G'
              mem_limit: '64G'
              environment:
                SPARK_DRIVER_MEMORY: "16g"
                SPARK_EXECUTOR_MEMORY: "16g"
              extra_resource_guarantees:
                nvidia.com/gpu: '1'
              extra_resource_limits:
                nvidia.com/gpu: '1'

# Hub resources
jupyter_hub_resources:
  requests:
    cpu: 500m
    memory: 1G
  limits:
    cpu: 2
    memory: 2G
```

#### Other Services

```yaml
prometheus_resources:
  requests:
    cpu: 2
    memory: 8Gi
  limits:
    cpu: 4
    memory: 8Gi

loki_resources:
  requests:
    cpu: 2
    memory: 8Gi
  limits:
    cpu: 4
    memory: 8Gi

grafana_resources:
  requests:
    cpu: 1
    memory: 2Gi
  limits:
    cpu: 2
    memory: 4Gi

temporal_resources:
  requests:
    cpu: 1
    memory: 4Gi
  limits:
    cpu: 2
    memory: 8Gi

superset_resources:
  requests:
    cpu: 1
    memory: 4Gi
  limits:
    cpu: 2
    memory: 8Gi

hive_resources:
  requests:
    cpu: 1
    memory: 4Gi
  limits:
    cpu: 2
    memory: 4Gi

ollama_resources:
  requests:
    cpu: 4
    memory: 32Gi
  limits:
    cpu: 16
    memory: 64Gi

open_webui_resources:
  requests:
    cpu: 1
    memory: 2Gi
  limits:
    cpu: 4
    memory: 4Gi
```

### Service-Specific Configuration

#### K3s

```yaml
# Cluster join token for servers and agents to authenticate (required, no default)
k3s_token: !vault |
      $ANSIBLE_VAULT;1.1;AES256
      ...encrypted...

# K3s version to install (leave unset to auto-detect latest stable)
k3s_version: v1.30.0+k3s1

# Additional arguments for k3s server (e.g., for containers)
k3s_extra_args: '--snapshotter=native'

# K3s data directory (default: /var/lib/rancher/k3s/storage)
base_dir: /var/lib/rancher/k3s/storage

# Linux group for kubectl access (default: root)
kubeconfig_group: docker
```

##### CoreDNS Customization

Scout can customize CoreDNS behavior by managing a `coredns-custom` ConfigMap in `kube-system`. This is primarily needed in air-gapped deployments where CoreDNS forwards unknown domains to `/etc/resolv.conf`, which may point to an upstream resolver (e.g., Tailscale MagicDNS) that gets overwhelmed with failing requests. It can also be used in non-air-gapped environments for DNS overrides.

The configuration uses a three-layer model:

**Layer 1 (automatic):** When `air_gapped: true`, CoreDNS automatically gets a deny-all NXDOMAIN default plus server blocks for `cluster.local` and reverse DNS, ensuring internal Kubernetes DNS resolution continues to work while blocking external lookups.

**Layer 2 (structured variable):** Use `coredns_forward_domains` for domain forwarding:

```yaml
# Domains to forward to /etc/resolv.conf (e.g., Tailscale, VPN domains)
coredns_forward_domains:
  - ts.net
```

**Layer 3 (escape hatch):** Use `coredns_extra_server_blocks` for arbitrary CoreDNS server blocks. This works with or without air-gapped mode. Keys are descriptive names; the `.server` suffix is auto-appended to form the ConfigMap data key.

```yaml
coredns_extra_server_blocks:
  scout-override: !unsafe |
    app.example.com:53 {
      template IN A app.example.com {
        answer "{{ .Name }} 60 IN A 198.51.100.10"
      }
    }
```

:::{note}
Values containing Go template syntax (e.g., `{{ .Name }}`) must use the `!unsafe` YAML tag to prevent Ansible from interpreting them as Jinja2 expressions.
:::

**ConfigMap key naming:** Keys ending in `.server` are loaded as additional CoreDNS server blocks. Keys ending in `.override` are loaded into the default server block. The air-gapped layer uses both (`airgap.override` and `airgap.server`), while domain lists and extra blocks use `.server` keys.

#### Traefik Ingress

```yaml
tls_cert_path: '/path/to/cert.pem'  # Optional TLS certificate
tls_key_path: '/path/to/key.pem'    # Optional TLS key
```

#### MinIO

```yaml
minio_volumes_per_server: 2  # Must be >= 2 if minio_hosts has > 1 node
```

#### Grafana Alerting

Configure alert notifications via Slack or email:

```
grafana_alert_contact_point: slack  # or 'email'

# Slack configuration:
slack_token: !vault |...
slack_channel_id: !vault |...

# Email configuration:
grafana_smtp_host: 'smtp.example.com:587'
grafana_smtp_user: !vault |...
grafana_smtp_password: !vault |...
grafana_smtp_from_address: 'scout@example.com'
grafana_smtp_from_name: 'Scout Alerts'
grafana_smtp_skip_verify: false
grafana_email_recipients: ['admin@example.com']
```

#### Ollama Models

Specify which AI models to pull automatically:

```yaml
ollama_models:
  - gpt-oss:120b
  - llama2
  - codellama

# Scout custom model (gpt-oss-120b-long:latest) is created by default
# Set to false to skip Scout model creation
scout_model_create: true

# For air-gapped deployments: shared NFS path for model storage
# Models are pulled to NFS on staging, mounted read-only on production
ollama_nfs_path: /mnt/nfs/ollama
```

See [Ollama model library](https://ollama.com/library) for available models.

#### HL7 Extractor

Scout ships with a default modality mapping file (`extractor/hl7-transformer/modality_mapping_codes.csv`) that is used to derive the `modality` column in the Delta Lake table. During deployment, this file is read and stored as a Kubernetes ConfigMap, which is then mounted into the hl7-transformer container at `/config/modality_mapping_codes.csv`.

The default mapping is based on WashU's exam codes. Sites using custom extractors would typically customize this file as part of their implementation. Sites using the standard extractor can override the mapping by setting `modality_map_source_file` in `inventory.yaml` to point to a custom CSV file.

As stated in the [Kubernetes documentation](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/#how-pods-with-resource-limits-are-run),
"The memory request is mainly used during (Kubernetes) Pod scheduling", so we recommend setting it to a small but viable value where the extractor could run
such as the one below to allow it to be scheduled. `hl7log_extractor_jvm_heap_max_ram_percentage` is passed into the container via `-XX:MaxRAMPercentage`. When in
a production setting with ample resources, there is more memory that can be allocated to the heap proportionally. The `75Gi` limit below was chosen by noting that
a large scale production instance took around 60GB of memory for the pod. If we make the assumption to be safe that all of that would go to the heap, that gives us
`60/0.8 = 75` to derive an appropriate limit.

```yaml
extractor_data_dir: /ceph/input/data  # Input directory for HL7 logs
# modality_map_source_file: /path/to/custom_modality_mapping.csv  # Optional: override default modality mapping

hl7log_extractor_resources:
  requests:
    cpu: 2
    memory: 4Gi
  limits:
    cpu: 4
    memory: 75Gi
hl7log_extractor_jvm_heap_max_ram_percentage: 80

hl7_transformer_spark_memory: 16G
hl7_transformer_cpu_request: 2
hl7_transformer_cpu_limit: 4
```

#### JupyterLab Extension Manager

Control whether users can install and manage JupyterLab extensions:

```yaml
# Extension Manager configuration
# Controls whether users can install/manage JupyterLab extensions
jupyter_extension_manager_mode: 'disabled'  # Options: 'disabled', 'readonly', 'enabled'
```

**Extension Manager Modes:**
- **`disabled`** (Scout default, recommended): Completely hides the Extension Manager icon from JupyterLab. Users cannot see or access extension installation UI. This is the recommended setting for air-gapped and production environments where extension installation is not desired.
- **`readonly`**: Shows the Extension Manager UI with a list of installed extensions. Users can enable or disable extensions that are already installed in the image, but cannot install new ones from PyPI.
- **`enabled`**: Full extension management capabilities. Users can search for, install, and manage extensions from PyPI. Only recommended for development environments with internet access and where users need to customize their JupyterLab environment.

:::{note}
In air-gapped environments, users cannot install extensions anyway due to lack of PyPI access. The `disabled` mode provides a cleaner user experience by hiding the non-functional Extension Manager UI.
:::

:::{warning}
Even with the Extension Manager disabled, users with terminal access can still run `jupyter labextension` commands. However, in air-gapped environments, these commands will fail due to lack of internet connectivity. The Extension Manager setting primarily controls the UI, not a comprehensive security lockdown.
:::

### Namespace Customization

Scout uses 6 consolidated namespaces to organize services by function. Default namespaces are defined in `roles/scout_common/defaults/main.yaml`. Override them if needed:

```yaml
k3s_cluster:
  vars:
    # Consolidated Scout namespaces (defaults shown)
    scout_core_namespace: scout-core           # PostgreSQL, Redis, Keycloak, OAuth2-Proxy, Launchpad
    scout_data_namespace: scout-data           # MinIO, Hive Metastore
    scout_extractor_namespace: scout-extractor # Cassandra, Elasticsearch, Temporal, Extractors
    scout_analytics_namespace: scout-analytics # Trino, Superset, JupyterHub, Chat/Open WebUI
    scout_operators_namespace: scout-operators # CloudNativePG, MinIO, K8ssandra, ECK, GPU operators
    scout_monitoring_namespace: scout-monitoring # Prometheus, Loki, Grafana

    # System namespaces
    traefik_namespace: kube-system            # Traefik (K3s system ingress)
    harbor_namespace: harbor                  # Harbor registry (air-gapped only)
```

Individual services inherit namespaces from these consolidated variables (e.g., `postgres_cluster_namespace: "{{ scout_core_namespace }}"`).
You can also override namespaces at the service level if you want to put a service into a different scout namespace or in its own dedicated namespace:

```yaml
k3s_cluster:
  vars:
    minio_tenant_namespace: "{{ scout_core_namespace }}"
    postgres_cluster_namespace: postgres
```

See `roles/scout_common/defaults/main.yaml` for the complete namespace mapping.

**Important:** Orchestration services (Temporal, Cassandra, Elasticsearch) must share the same namespace for proper operation. They cannot be separated into different namespaces because cross-namespace secret access is not supported. These services always use `scout_extractor_namespace`.

(air-gapped-deployment)=
## Air-Gapped Deployment

Scout supports air-gapped deployments for environments without internet access on production nodes.

**Important:** Air-gapped deployments require Rocky Linux 9 on production k3s nodes due to SELinux package dependencies.

Ansible automatically deploys K3s and Harbor on a staging node when air-gapped mode is enabled. You only need to define the staging host in your inventory and run the playbooks.

For complete air-gapped deployment documentation, see [Air-Gapped Deployment Guide](air-gapped.md).

### Quick Setup

1. Set `air_gapped: true` in inventory
2. Define staging node in inventory (see [Air-Gapped Deployment Guide](air-gapped.md))
3. Run playbooks: `make all`

See [Air-Gapped Deployment Guide](air-gapped.md) for detailed instructions.

## Configuration Hierarchy

Scout uses Ansible's variable precedence system. Understanding this helps you know where to set values:

1. **Role defaults** (lowest precedence)
   - `roles/scout_common/defaults/main.yaml` - Shared defaults
   - `roles/*/defaults/main.yaml` - Role-specific defaults
   - **Can be overridden by inventory.yaml** ✓

2. **Inventory vars** (medium precedence) ← **Your overrides go here**
   - `inventory.yaml`
   - Environment-specific config, secrets, resource sizes
   - **Overrides all role defaults** ✓
   - **Cannot override group_vars** ✗

3. **Group vars** (higher precedence)
   - `group_vars/all/versions.yaml` - Component versions
   - Managed centrally (e.g., by Renovate)
   - **Cannot be overridden by inventory** ✗
   - Override with `-e` flag for testing

4. **Extra vars** (highest precedence)
   - Command line: `-e variable=value`
   - Overrides everything

### Best Practices

- Put configuration in `inventory.yaml`
- Don't try to override versions in inventory (they're in `group_vars/all/versions.yaml`)
- Use `-e` flag to test different versions temporarily:
  ```bash
  ansible-playbook -e "k3s_version=v1.30.0+k3s1" playbooks/k3s.yaml
  ```

(testing-upgrades)=
### Testing Upgrades

**Always test version upgrades in your staging environment before applying them to production.** This practice minimizes the risk of unexpected issues and allows you to validate compatibility before impacting production workloads.

**Recommended upgrade workflow:**

1. **Test in staging:** Use the `-e` flag to override versions in your staging environment
   ```bash
   # Example: Testing k3s upgrade
   ansible-playbook -i inventory.staging.yaml -e "k3s_version=v1.35.0+k3s1" playbooks/k3s.yaml

   # Example: Testing multiple component upgrades
   ansible-playbook -i inventory.staging.yaml \
     -e "k3s_version=v1.35.0+k3s1" \
     -e "temporal_version=0.68.0" \
     playbooks/main.yaml
   ```

2. **Validate staging deployment:** Verify that all services start correctly, run integration tests, and check for compatibility issues

3. **Update versions centrally:** Once validated, update `group_vars/all/versions.yaml` to apply the new versions to all environments

4. **Deploy to production:** Run the standard deployment without version overrides (uses versions from `group_vars/all/versions.yaml`)
   ```bash
   ansible-playbook -i inventory.yaml playbooks/k3s.yaml
   ```

This approach ensures that version changes are thoroughly tested before they reach production, reducing the likelihood of failed upgrades or service disruptions.

## Validating Your Inventory

### Check Configuration Loading

Verify Ansible can parse your inventory and load variables:

```bash
# List all hosts and groups
ansible-inventory -i inventory.yaml --list

# Show variables for a specific host
ansible-inventory -i inventory.yaml --host leader.example.edu

# Check syntax
ansible-inventory -i inventory.yaml --list > /dev/null
```

### Test Connectivity

Verify SSH connectivity and privilege escalation:

```bash
# Test SSH connection
ansible -i inventory.yaml all -m ping

# Test sudo access
ansible -i inventory.yaml all -m shell -a "whoami" --become
```

### Common Issues

**Vault decryption fails:**
- Ensure `vault/pwd.sh` is executable and returns the correct password
- Set `ANSIBLE_VAULT_PASSWORD_FILE` environment variable:
  ```bash
  export ANSIBLE_VAULT_PASSWORD_FILE=vault/pwd.sh
  ```

**SSH connection fails:**
- Verify `ansible_user` has SSH key access to nodes
- Check `ansible_host` resolves correctly
- Test manual SSH: `ssh ansible_user@ansible_host`

**Sudo password fails:**
- Verify `ansible_become_password` is encrypted correctly
- Test manual sudo: `ssh ansible_user@ansible_host sudo whoami`

## Next Steps

After creating your `inventory.yaml`:

1. **Test connectivity:** Run `ansible -i inventory.yaml all -m ping`
2. **Review the deployment:** Check `ansible/README.md` for deployment commands
3. **Deploy Scout:** Run `make all` from the `ansible/` directory
4. **Monitor deployment:** Check pod status with `kubectl get pods -A`

For more information, see:
- `ansible/README.md` in the Scout repository
- [Ansible Inventory Documentation](https://docs.ansible.com/ansible/latest/inventory_guide/intro_inventory.html)
- [Ansible Vault Documentation](https://docs.ansible.com/ansible/latest/vault_guide/index.html)
