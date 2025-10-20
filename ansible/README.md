# Scout Ansible Deployment

Automated deployment and configuration management for Scout using Ansible.

## Overview

Scout uses Ansible to orchestrate deployment of a distributed data analysis platform on Kubernetes (K3s). The platform includes:

- **Data Lake**: MinIO object storage and Hive metastore for data persistence
- **Analytics**: Trino query engine and Apache Superset for data visualization
- **Orchestrator**: Temporal workflow engine with Cassandra and Elasticsearch
- **Extractor**: HL7 log processing and transformation services
- **Notebooks**: JupyterHub with PySpark for interactive data analysis
- **Monitoring**: Prometheus, Loki, and Grafana for observability
- **Explorer**: Web-based data exploration interface

## Prerequisites

### Required Software

- [Ansible](https://docs.ansible.com/ansible/latest/installation_guide/intro_installation.html) 2.14 or later
- `make` (for using Makefile targets)
- SSH access to target nodes
- Sufficient storage on target nodes (see storage requirements below)

### Infrastructure Requirements

**Minimum:**
- 1 server node (control plane + worker)
- 16 CPU cores
- 64GB RAM
- 500GB storage

**Recommended:**
- 1 server node (control plane)
- 2+ worker nodes
- GPU node(s) for AI/ML workloads (optional)
- Dedicated staging node for air-gapped deployments (optional)

### Storage Recommendations

Default storage allocations (customize in `inventory.yaml`):
- MinIO: 750Gi (data lake storage)
- Cassandra: 300Gi (Temporal persistence)
- Elasticsearch: 100Gi (Temporal visibility)
- PostgreSQL: 100Gi (application databases)
- Prometheus: 100Gi (metrics)
- Loki: 100Gi (logs)
- Jupyter: 250Gi (user notebooks)

## Quick Start

### 1. Prepare Inventory

Copy the example inventory and customize for your environment:

```bash
cp inventory.example.yaml inventory.yaml
```

Edit `inventory.yaml` to configure:
- **Hosts**: Add your server and worker node FQDNs
- **Paths**: Customize storage directories for your disk layout
- **Secrets**: Generate and encrypt passwords using Ansible Vault (see below)
- **Resources**: Adjust CPU, memory, and storage sizes for your environment

### 2. Configure Secrets with Ansible Vault

Create a vault password script:

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

Generate encrypted secrets:

```bash
# Generate and encrypt a random password
openssl rand -hex 32 | ansible-vault encrypt_string --vault-password-file vault/pwd.sh

# Encrypt an environment variable
echo $MY_PASSWORD | ansible-vault encrypt_string --vault-password-file vault/pwd.sh
```

Paste the encrypted values into `inventory.yaml` for sensitive variables like:
- Database passwords
- S3 credentials
- API tokens
- OAuth secrets

See the [Ansible Vault documentation](https://docs.ansible.com/ansible/latest/vault_guide/vault_managing_passwords.html) for more options.

### 3. Deploy Scout

Install Ansible dependencies and deploy:

```bash
make all
```

This runs the `playbooks/main.yaml` playbook which orchestrates the complete Scout installation.

## Deployment Options

### Full Deployment

Deploy all Scout components:

```bash
make all
```

### Component Deployment

Deploy individual components:

```bash
make install-k3s          # Install K3s cluster
make install-postgres     # PostgreSQL databases
make install-lake         # MinIO + Hive metastore
make install-analytics    # Trino + Superset
make install-orchestrator # Temporal + Cassandra + Elasticsearch
make install-extractor    # HL7 processing services
make install-jupyter      # JupyterHub
make install-monitor      # Prometheus + Loki + Grafana
make install-explorer     # Explorer web interface
```

### Development Tools

Optional development and testing tools:

```bash
make install-orthanc      # Orthanc PACS server
make install-dcm4chee     # DCM4CHEE PACS server
make install-mailhog      # Email testing tool
```

## Air-Gapped Deployment

Scout supports air-gapped deployments for environments without direct internet access.

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

### Setup

1. **Define staging node** in `inventory.yaml`:
   ```yaml
   staging:
     hosts:
       staging.example.edu:
         ansible_host: staging
   ```

2. **Enable air-gapped mode** in `inventory.yaml`:
   ```yaml
   all:
     vars:
       air_gapped: true
   ```

3. **Deploy staging infrastructure**:
   ```bash
   ansible-playbook -i inventory.yaml playbooks/staging-k3s.yaml
   ansible-playbook -i inventory.yaml playbooks/harbor.yaml
   ```

4. **Deploy Scout** from staging node. It will automatically use Harbor for image pulls
    ```bash
    make all
    ```

See `inventory.example.yaml` for detailed air-gapped configuration examples.

### Version Configuration

All component versions are managed in a single file:

```
ansible/group_vars/all/versions.yaml
```

This includes versions for:
- Helm charts (K8ssandra, ECK, Temporal, etc.)
- Docker images (Superset, Jupyter, extractors, etc.)
- Operators (GPU, MinIO, PostgreSQL, etc.)
- Infrastructure (K3s, Helm plugins, etc.)

See [VERSION_MANAGEMENT.md](VERSION_MANAGEMENT.md) for complete documentation.

### Overriding Versions

Versions in `group_vars/all/versions.yaml` have higher precedence than inventory and ensure consistency across environments.

**For testing with different versions, use the `-e` flag:**

```bash
# Test with a specific K3s version
ansible-playbook playbooks/k3s.yaml -e "k3s_version=v1.31.0+k3s1"

# Test with a specific Elasticsearch version
ansible-playbook playbooks/orchestrator.yaml -e "elasticsearch_version=9.0.0"

# Override multiple versions
ansible-playbook playbooks/main.yaml \
  -e "k3s_version=v1.31.0+k3s1" \
  -e "elasticsearch_version=9.0.0"
```

**For permanent version changes, edit `group_vars/all/versions.yaml`.**

## Customization

### Configuration Hierarchy

Scout uses Ansible's variable precedence system (low to high):

1. **Role defaults** (`roles/scout_common/defaults/main.yaml`) - **Lowest**
    - All shared defaults: namespaces, S3 config, timeouts, etc.
    - **Can be overridden by inventory.yaml** ✓
2. **Role defaults** (`roles/*/defaults/main.yaml`)
    - Role-specific defaults
    - **Can be overridden by inventory.yaml** ✓
3. **Inventory vars** (`inventory.yaml`) - **Your overrides go here**
    - Environment-specific config, secrets, paths, resource sizes
    - **Overrides all role defaults** ✓
4. **Group vars** (`group_vars/all/versions.yaml`) - **Higher than inventory**
    - Component versions (managed by Renovate)
    - **Cannot be overridden by inventory** (use `-e` flag instead)
5. **Extra vars** (`-e` flag) - **Highest**

**Key Points:**
- ✅ **scout_common defaults** can be overridden in `inventory.yaml`
- ❌ **versions.yaml** cannot be overridden in `inventory.yaml` (this is intentional for consistency)
- ✅ Use `-e` flag to override versions for testing: `ansible-playbook -e "k3s_version=v1.30.0+k3s1" ...`

### Common Customizations

**Storage paths** (in `inventory.yaml`):
```yaml
scout_repo_dir: /scout/data/scout
minio_dir: /scout/data/minio
postgres_dir: /scout/persistence/postgres
# ... customize other paths
```

**Resource allocations** (in `inventory.yaml`):
```yaml
# Trino memory allocation
trino_worker_memory_gb: 32
trino_coordinator_memory_gb: 16

# PostgreSQL resources
postgres_resources:
  requests:
    cpu: 8
    memory: 128Gi
```

**Jupyter GPU resources** (in `inventory.yaml`):
```yaml
jupyter_singleuser_extra_resource:
  guarantees:
    nvidia.com/gpu: '1'
  limits:
    nvidia.com/gpu: '1'
```

### Namespaces

Default Kubernetes namespaces are defined in `roles/scout_common/defaults/main.yaml` and can be overridden in `inventory.yaml`:

```yaml
k3s_cluster:
  vars:
    # Override default namespaces
    postgres_cluster_namespace: postgres  # Default: cloudnative-pg
    jupyter_namespace: custom-jupyter     # Default: jupyter
    superset_namespace: custom-superset   # Default: superset
```

See `roles/scout_common/defaults/main.yaml` for all available namespace variables.

## GPU Support

Scout supports NVIDIA GPUs for accelerated workloads (JupyterHub, AI models).

### Setup

1. **Add GPU nodes** to `inventory.yaml`:
   ```yaml
   gpu_workers:
     hosts:
       gpu-node-1.example.edu:
   ```

2. **Deploy GPU operator**:
   ```bash
   make install-k3s  # Includes GPU operator
   ```

3. **Configure Jupyter** for GPU access (in `inventory.yaml`):
   ```yaml
   jupyter_singleuser_extra_resource:
     guarantees:
       nvidia.com/gpu: '1'
     limits:
       nvidia.com/gpu: '1'
   ```

The NVIDIA GPU Operator handles driver installation and device plugin configuration.

## Accessing Services

After deployment, services are available within the cluster. Access methods:

### Port Forwarding (Development)

Forward services to localhost:

```bash
# Grafana
kubectl port-forward -n grafana service/grafana 3000:80

# Temporal Web UI
kubectl port-forward -n temporal service/temporal-web 8080:8080

# Prometheus
kubectl port-forward -n prometheus service/prometheus-server 9090:80

# JupyterHub
kubectl port-forward -n jupyter service/proxy-public 8000:80
```

Then access at `http://localhost:<port>`

### Ingress (Production)

Configure external access using Traefik ingress (deployed by default):

1. Set `external_url` in `inventory.yaml`
2. Configure DNS to point to your K3s node
3. Optionally configure TLS certificates via `tls_cert_path` and `tls_key_path`

## Monitoring and Observability

Scout includes a complete monitoring stack:

- **Prometheus**: Metrics collection and alerting
- **Loki**: Log aggregation and querying
- **Grafana**: Unified dashboards and visualizations

Pre-configured dashboards monitor:
- Kubernetes cluster health
- Application metrics
- Database performance
- Storage utilization

Access Grafana via port-forward or ingress to view dashboards.

## Troubleshooting

### Check Ansible Variables

Verify configuration is loaded correctly:

```bash
ansible-inventory -i inventory.yaml --list
ansible-inventory -i inventory.yaml --host <hostname>
```

### Check Pod Status

```bash
# All pods
kubectl get pods -A

# Specific namespace
kubectl get pods -n temporal
kubectl logs -n temporal <pod-name>
```

### Re-run Specific Components

Re-deploy a single component:

```bash
make install-postgres  # Re-run PostgreSQL deployment
make install-monitor   # Re-run monitoring stack
```

### Ansible Debugging

Enable debug mode:

```bash
DEBUG=1 make install-analytics
```

Check diff without making changes:

```bash
ANSIBLE_CMD="--check --diff" make install-lake
```

### Common Issues

**Storage not created:**
- Verify paths exist and are writable on target nodes
- Check storage role tasks: `ansible-playbook playbooks/lake.yaml --tags storage`

**Image pull failures (air-gapped):**
- Verify Harbor is accessible from K3s nodes
- Check registry mirror configuration in K3s
- Ensure images are cached in Harbor

**Helm deployment timeouts:**
- Increase timeout: `helm_chart_timeout: 15m` in role defaults
- Check pod events: `kubectl describe pod <pod-name>`

## Maintenance

### Updating Components

Update component versions and redeploy:

```bash
# Update versions in group_vars/all/versions.yaml

# Redeploy component
make install-analytics
```

### Scaling

Scale worker nodes:

1. Add nodes to `workers` group in `inventory.yaml`
2. Run: `make install-k3s`
3. Verify: `kubectl get nodes`

Scale service replicas by adjusting replica counts in role defaults.

## Project Structure

```
ansible/
├── collections/requirements.yaml # Ansible collection dependencies
├── group_vars/all/
│   └── versions.yaml             # Centralized version management
├── inventory.example.yaml        # Example inventory (copy to inventory.yaml)
├── playbooks/                    # Ansible playbooks
│   ├── main.yaml                 # Main orchestration playbook
│   ├── k3s.yaml                  # K3s installation
│   ├── lake.yaml                 # Data lake (MinIO + Hive)
│   ├── analytics.yaml            # Analytics (Trino + Superset)
│   ├── orchestrator.yaml         # Temporal workflow engine
│   └── ...                       # Additional service playbooks
├── roles/
│   ├── scout_common/            # Shared defaults and helper tasks
│   │   ├── defaults/main.yaml   # ALL Scout defaults (override in inventory)
│   │   ├── tasks/               # Shared tasks (deploy_helm_chart, etc.)
│   │   └── meta/main.yaml       # Role metadata
│   ├── cassandra/               # Cassandra database
│   ├── elasticsearch/           # Elasticsearch
│   ├── temporal/                # Temporal workflow
│   ├── minio/                   # MinIO object storage
│   ├── trino/                   # Trino query engine
│   ├── superset/                # Apache Superset
│   ├── jupyter/                 # JupyterHub
│   ├── prometheus/              # Prometheus monitoring
│   ├── loki/                    # Loki log aggregation
│   ├── grafana/                 # Grafana dashboards
│   └── ...                      # Additional roles
├── Makefile                     # Convenience targets
└──README.md                     # This file
```

## Additional Resources

- [Ansible Documentation](https://docs.ansible.com/)
- [K3s Documentation](https://docs.k3s.io/)

## License

See the main Scout repository for license information.
