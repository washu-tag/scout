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

For air-gapped deployments, define a staging node with internet access that runs Harbor registry:

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
    harbor_dir: /scout/persistence/harbor
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

#### Local Paths

Define where data will be stored on your nodes:

```yaml
base_dir: /var/lib/rancher/k3s/storage  # K3s container images
scout_repo_dir: /scout/data/scout       # Scout repository
minio_dir: /scout/data/minio            # MinIO data
cassandra_dir: /scout/persistence/cassandra
elasticsearch_dir: /scout/persistence/elasticsearch
postgres_dir: /scout/persistence/postgres
prometheus_dir: /scout/monitoring/prometheus
loki_dir: /scout/monitoring/loki
grafana_dir: /scout/monitoring/grafana
jupyter_dir: /scout/data/jupyter
ollama_dir: /scout/persistence/ollama
open_webui_dir: /scout/persistence/openwebui
extractor_data_dir: /ceph/input/data    # HL7 log input directory
```

**Best practice:** Organize paths by purpose:
- `/scout/data/*` - Application data
- `/scout/persistence/*` - Database persistence
- `/scout/monitoring/*` - Monitoring and logs

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
  max_connections: '100'
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
```

Memory is computed automatically from heap size (requests = 1x heap, limits = 2x heap).

**Query Memory Limits:**
- `query.max-memory-per-node` is set to `heap_size × trino_per_node_query_memory_fraction` (default 30%)
- `query.max-memory` (cluster-wide) is calculated as `worker_count × worker_heap × trino_per_node_query_memory_fraction`
- These limits scale automatically with worker count and heap size changes
- Only override `trino_per_node_query_memory_fraction` if you understand [Trino's memory management](https://trino.io/docs/current/admin/properties-resource-management.html)

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

#### JupyterHub

```yaml
# Spark memory for notebook containers
# Note: Use JupyterHub format (K, M, G, T) not Kubernetes format (Ki, Mi, Gi, Ti)
jupyter_spark_memory: 8G

# CPU resources
# Note: JupyterHub Helm chart 4.3.x requires numeric values (not strings like "250m")
# Use fractional cores (0.25 = 250 millicores) or whole numbers
jupyter_singleuser_cpu_request: 2
jupyter_singleuser_cpu_limit: 8

# Optional: Override memory for non-Spark workloads
# By default, memory is computed from spark_memory (1x request, 2x limit)
# Note: JupyterHub requires decimal suffixes (K, M, G, T), not binary (Ki, Mi, Gi, Ti)
# jupyter_singleuser_memory_request: 16G
# jupyter_singleuser_memory_limit: 32G

# Hub resources
jupyter_hub_resources:
  requests:
    cpu: 500m
    memory: 1G
  limits:
    cpu: 2
    memory: 2G

# GPU resources (if using gpu_workers)
jupyter_singleuser_extra_resource:
  guarantees:
    nvidia.com/gpu: '1'
  limits:
    nvidia.com/gpu: '1'
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

```
k3s_token: !vault |...  # Cluster join token
kubeconfig_group: 'docker'  # Linux group for kubectl access
```

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
```

See [Ollama model library](https://ollama.com/library) for available models.

#### HL7 Extractor

```yaml
extractor_data_dir: /ceph/input/data  # Input directory for HL7 logs

hl7log_extractor_resources:
  requests:
    cpu: 2
    memory: 4Gi
  limits:
    cpu: 4
    memory: 8Gi

hl7_transformer_spark_memory: 16G
hl7_transformer_cpu_request: 2
hl7_transformer_cpu_limit: 4
```

### Namespace Customization

Default namespaces are defined in `roles/scout_common/defaults/main.yaml`. Override them if needed:

```yaml
k3s_cluster:
  vars:
    postgres_cluster_namespace: postgres      # Default: cnpg
    jupyter_namespace: custom-jupyter         # Default: jupyter
    superset_namespace: custom-superset       # Default: superset
    temporal_namespace: custom-temporal       # Default: temporal
    # ... and so on for other services
```

See `roles/scout_common/defaults/main.yaml` for the complete list of namespace variables.

(air-gapped-deployment)=
## Air-Gapped Deployment

Scout supports air-gapped deployments for environments without internet access on production nodes.

### Architecture

```
┌──────────────┐         ┌──────────────────────────────┐
│   Internet   │────────▶│  Staging Node (Harbor)       │
└──────────────┘         │  - K3s cluster               │
                         │  - Harbor registry proxy     │
                         └──────┬───────────────────────┘
                                │
                         Air Gap│ (registry mirrors)
                                │
                         ┌──────▼───────────────────────┐
                         │  Production K3s Cluster      │
                         │  - No internet access        │
                         │  - Pulls images via Harbor   │
                         └──────────────────────────────┘
```

### Setup Steps

#### 1. Define Staging Node

Add a staging host with internet access:

```
staging:
  hosts:
    staging.example.edu:
      ansible_host: staging
      ansible_python_interpreter: /usr/bin/python3
  vars:
    staging_k3s_token: !vault |...
    harbor_admin_password: !vault |...
    harbor_storage_size: 100Gi
    harbor_dir: /scout/persistence/harbor
```

#### 2. Enable Air-Gapped Mode

Set the global air-gapped flag:

```yaml
all:
  vars:
    air_gapped: true
```

#### 3. Deploy Staging Infrastructure

Deploy K3s and Harbor on the staging node:

```bash
ansible-playbook -i inventory.yaml playbooks/staging-k3s.yaml
ansible-playbook -i inventory.yaml playbooks/harbor.yaml
```

#### 4. Configure Production Cluster

The production K3s cluster will automatically be configured with registry mirrors pointing to Harbor. Ensure the K8s API (port 6443) is accessible from your Ansible control machine.

#### 5. Deploy Scout

Deploy Scout normally. Helm charts will be deployed from localhost, and container images will be pulled through Harbor:

```bash
make all
```

### Requirements for Air-Gapped Mode

- Staging node must have internet access
- Harbor must be deployed on staging node before Scout deployment
- Production K3s nodes must have network access to Harbor
- K8s API (port 6443) must be accessible from Ansible control machine
- Kubeconfig for production cluster must be accessible from control machine

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
