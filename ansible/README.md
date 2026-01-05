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
- **Launchpad**: Central landing page and service navigation hub
- **AI/ML**: Open WebUI with Ollama for AI-powered chat

## Prerequisites

### Required Software

- [Ansible](https://docs.ansible.com/ansible/latest/installation_guide/intro_installation.html) 2.20 or later
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
make install-launchpad    # Launchpad web interface
make install-chat         # Open WebUI + Ollama
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

3. **Deploy staging infrastructure** (k3s, traefik, and harbor):
   ```bash
   make install-staging
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
    - Component versions (to be managed by Renovate, probably)
    - **Cannot be overridden by inventory** (use `-e` flag instead)
5. **Extra vars** (`-e` flag) - **Highest**

**Key Points:**
- ✅ **scout_common defaults** can be overridden in `inventory.yaml`
- ❌ **versions.yaml** cannot be overridden in `inventory.yaml` (this is intentional for consistency)
- ✅ Use `-e` flag to override versions for testing: `ansible-playbook -e "k3s_version=v1.30.0+k3s1" ...`

### Common Customizations

**Scout repository path** (in `inventory.yaml`):
```yaml
# Path to scout repo on localhost (Ansible control node)
# Used for scout-local helm charts, analytics files, logos, and kubeconfig storage
# Default: repo root (assumes running Ansible from within cloned repo)
scout_repo_dir: /path/to/scout

# Clone/update repo before deployment (default: false)
# Set to true to clone or update the repo to a different location
clone_scout_repo: true
```

**Storage paths** (in `inventory.yaml`):
```yaml
minio_dir: /scout/data/minio
postgres_dir: /scout/persistence/postgres
# ... customize other paths
```

**Resource allocations** (in `inventory.yaml`):
```yaml
# JVM-based services
# Cassandra/Trino: requests = 1x heap, limits = 2x heap
cassandra_max_heap: 12G
trino_worker_max_heap: 16G
trino_coordinator_max_heap: 8G

# Elasticsearch: requests = 2x heap, limits = 4x heap (follows Elastic best practices)
elasticsearch_max_heap: 3G  # Keep ≤ 26GB for compressed OOPs

# PostgreSQL resources
postgres_resources:
  requests:
    cpu: 8
    memory: 128Gi
```

**Partial resource overrides** (in `inventory.yaml`):

Most services support partial resource overrides. You can override only the parts you want to change, and defaults will be used for the rest:

```yaml
# Override only limits, keep default requests
temporal_resources:
  limits:
    cpu: 4
    memory: 8Gi

# Override only one value within limits
prometheus_resources:
  limits:
    memory: 4Gi

# Override both requests and limits (full override)
grafana_resources:
  requests:
    cpu: 500m
    memory: 1Gi
  limits:
    cpu: 2
    memory: 2Gi
```

This works through Ansible's `combine` filter with `recursive=true`. Role defaults define the base values (e.g., `temporal_resources_default`), and your inventory overrides (e.g., `temporal_resources`) are merged on top.

**Services supporting partial resource overrides:**
- temporal, postgres, minio, hive, prometheus, grafana, loki
- superset, superset_statsd, jupyter_hub, hl7log_extractor
- redis_operator, redis_cluster_node, voila, orthanc, dcm4chee
- ollama, open_webui, mcp_trino

**Services NOT supporting partial overrides** (use flattened variables instead):
- `trino_coordinator_*`, `trino_worker_*` - Use `trino_coordinator_max_heap`, `trino_coordinator_cpu_limit`, etc.
- `cassandra` - Use `cassandra_max_heap`, `cassandra_cpu_request`, etc.
- `elasticsearch` - Use `elasticsearch_max_heap`, `elasticsearch_cpu_request`, etc.
- `hl7_transformer` - Use `hl7_transformer_spark_memory`, `hl7_transformer_cpu_limit`, etc.

These services use flattened variables because JVM heap sizes drive memory calculations with different multipliers for requests vs limits.

**Jupyter profiles** (in `inventory.yaml`):

See [Customizing JupyterHub Profiles](#customizing-jupyterhub-profiles) for profile configuration including GPU support.

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

## Custom Filter Plugins

Scout includes custom Jinja2 filter plugins to extend Ansible's templating capabilities. These filters help standardize complex transformations and conversions across roles.

### Available Filters

#### `jvm_memory_to_k8s`

Converts JVM heap sizes to Kubernetes memory specifications with proper binary unit handling.

**Background**: JVM interprets `-Xmx2G` as 2 gibibytes (binary, 1024³), but Kubernetes interprets `2G` as 2 gigabytes (decimal, 1000³). This filter ensures JVM and Kubernetes use matching binary units to prevent out-of-memory errors.

**Usage:**
```yaml
# Simple conversion (1x)
memory: "{{ cassandra_max_heap | jvm_memory_to_k8s }}"
# Input: "2G" → Output: "2Gi"

# With multiplier (2x for limits to account for off-heap memory)
memory: "{{ cassandra_max_heap | jvm_memory_to_k8s(2) }}"
# Input: "2G" → Output: "4Gi"
```

**Supported units** (case-insensitive):
- `K`/`k` → `Ki` (kibibytes)
- `M`/`m` → `Mi` (mebibytes)
- `G`/`g` → `Gi` (gibibytes)

**Smart conversion examples:**
- `"1024M"` → `"1Gi"` (automatically converts to larger unit)
- `"512M"` → `"512Mi"` (keeps original unit)
- `"512M" | jvm_memory_to_k8s(2)` → `"1Gi"` (multiplies then converts)

**Services using this filter:**
- Cassandra (JVM heap → container memory: 1x request, 2x limit)
- Elasticsearch (JVM heap → container memory: 2x request, 4x limit)
- Trino (JVM heap → container memory: 1x request, 2x limit)
- HL7 Transformer (Spark memory → container memory: 1x request, 2x limit)

#### `multiply_memory`

Multiplies a memory specification by a factor while preserving the unit. Used for services that require decimal suffixes (K, M, G, T) rather than Kubernetes binary suffixes (Ki, Mi, Gi, Ti).

**Usage:**
```yaml
# Double the memory (for limits)
memory: "{{ hl7_transformer_spark_memory | multiply_memory(2) }}"
# Input: "8G" → Output: "16G"

# Triple the memory
memory: "{{ hl7_transformer_spark_memory | multiply_memory(3) }}"
# Input: "8G" → Output: "24G"
```

**Supported units** (case-insensitive):
- `K`/`k` → `K` (kilobytes)
- `M`/`m` → `M` (megabytes)
- `G`/`g` → `G` (gigabytes)
- `T`/`t` → `T` (terabytes)

**Use cases:**
- Services that require decimal suffixes, not Kubernetes binary format

**Services using this filter:**
- HL7 Transformer (Spark memory → container limits)

### Creating Custom Filters

1. **Create filter plugin file** in `filter_plugins/`:

```python
#!/usr/bin/env python3
"""
Custom Jinja2 filters for Scout.
"""

def my_custom_filter(input_value, arg1="default"):
    """
    Description of what your filter does.

    Args:
        input_value: The input to transform
        arg1: Optional argument

    Returns:
        Transformed output
    """
    # Your transformation logic here
    return f"{input_value}-{arg1}"


class FilterModule(object):
    """Ansible filter plugin class."""

    def filters(self):
        return {
            'my_custom_filter': my_custom_filter,
        }
```

2. **Use filter in templates or defaults**:

```yaml
result: "{{ my_variable | my_custom_filter }}"
result_with_arg: "{{ my_variable | my_custom_filter('custom') }}"
```

3. **Create unit tests** in `tests/unit/filter_plugins/test_*.py`:

```python
import sys
from pathlib import Path

# Add filter_plugins to path so we can import the module
filter_plugins_path = Path(__file__).parent.parent.parent.parent / "filter_plugins"
sys.path.insert(0, str(filter_plugins_path))

import pytest
from my_filters import my_custom_filter

def test_my_custom_filter():
    assert my_custom_filter("test") == "test-default"
    assert my_custom_filter("test", "custom") == "test-custom"
```

4. **Run tests** using `uvx`:

```bash
uvx pytest tests/unit/filter_plugins/test_my_filters.py -v
```

### Testing Filter Plugins

Scout uses pytest for filter plugin testing. Tests are located in `tests/unit/filter_plugins/`.

**Run all filter tests:**
```bash
uvx pytest tests/unit/filter_plugins/ -v
```

**Run specific test file:**
```bash
uvx pytest tests/unit/filter_plugins/test_jvm_memory.py -v
```

**Run specific test:**
```bash
uvx pytest tests/unit/filter_plugins/test_jvm_memory.py::TestJvmMemoryToK8s::test_gigabytes_uppercase -v
```

### Filter Plugin Best Practices

1. **Document thoroughly**: Include docstrings with examples
2. **Handle edge cases**: Validate inputs and provide clear error messages
3. **Write comprehensive tests**: Cover normal cases, edge cases, and error conditions
4. **Keep filters focused**: Each filter should do one thing well
5. **Use type hints**: Make function signatures clear with Python type hints
6. **Test with actual data**: Include tests using real values from inventory

### Example: JVM Memory Filter

See `filter_plugins/jvm_memory.py` and `tests/unit/filter_plugins/test_jvm_memory.py` for a complete example of:
- Unit conversion with validation
- Smart unit selection (e.g., `1024M` → `1Gi`)
- Multiplier support for derived values
- Comprehensive test coverage (25 test cases)
- Real-world usage in multiple roles

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

3. **GPU profile configuration**:

   By default, a GPU profile is NOT included in the dev configuration. For production deployments with GPU nodes, override `jupyter_profiles` in `inventory.yaml` to add GPU support. See [Customizing JupyterHub Profiles](#customizing-jupyterhub-profiles) for complete examples including GPU profiles with custom resource allocations.

The NVIDIA GPU Operator handles driver installation and device plugin configuration.

## Customizing JupyterHub Profiles

Scout provides a default "CPU Only" profile with size options (Small, Medium, Large):

- **Small**: 0.5 CPU guarantee, 1 CPU limit, 1G memory guarantee, 4G memory limit
- **Medium**: 1 CPU guarantee, 2 CPU limit, 2G memory guarantee, 8G memory limit
- **Large**: 2 CPU guarantee, 4 CPU limit, 4G memory guarantee, 16G memory limit

These default profiles can be overridden by defining your own custom profiles in `inventory.yaml`.

### Adding to Default Profiles

To add GPU support while keeping the default CPU profile:

```yaml
jupyter_profiles:
  - "{{ jupyter_cpu_profile }}"  # Include default CPU profile
  - display_name: "GPU"
    slug: "gpu"
    description: "GPU environment for ML/AI workloads"
    default: false
    kubespawner_override:
      cpu_guarantee: 2
      cpu_limit: 8
      mem_guarantee: '8G'
      mem_limit: '32G'
      environment:
        SPARK_DRIVER_MEMORY: "24g"
        SPARK_EXECUTOR_MEMORY: "24g"
      extra_resource_guarantees:
        nvidia.com/gpu: '1'
      extra_resource_limits:
        nvidia.com/gpu: '1'
```

### Completely Replacing Default Profiles

To completely replace the default profiles with your own custom profiles, define `jupyter_profiles` without including `jupyter_cpu_profile`:

**Example 1: Simple profiles with profile-level `kubespawner_override`**

Each profile has fixed resources defined at the profile level:

```yaml
jupyter_profiles:
  - display_name: "Development"
    slug: "dev"
    description: "Lightweight environment for development"
    default: true
    kubespawner_override:  # Profile-level override
      cpu_guarantee: 0.5
      cpu_limit: 2
      mem_guarantee: '1G'
      mem_limit: '4G'
      environment:
        SPARK_DRIVER_MEMORY: "3g"
        SPARK_EXECUTOR_MEMORY: "3g"

  - display_name: "Production"
    slug: "prod"
    description: "High-performance environment for production workloads"
    kubespawner_override:  # Profile-level override
      cpu_guarantee: 4
      cpu_limit: 8
      mem_guarantee: '8G'
      mem_limit: '32G'
      environment:
        SPARK_DRIVER_MEMORY: "24g"
        SPARK_EXECUTOR_MEMORY: "24g"
```

**Example 2: Profiles with size options using profile_option-level `kubespawner_override`**

Each profile has multiple size choices, with resources defined at the profile_option level:

```yaml
jupyter_profiles:
  - display_name: "Standard Compute"
    slug: "standard"
    description: "Standard CPU-based environment"
    default: true
    profile_options:
      resource_allocation:
        display_name: "Resource Size"
        choices:
          small:
            display_name: "Small (2 CPU, 8Gi RAM)"
            default: true
            kubespawner_override:  # Option-level override
              cpu_guarantee: 1
              cpu_limit: 2
              mem_guarantee: '2G'
              mem_limit: '8G'
              environment:
                SPARK_DRIVER_MEMORY: "6g"
                SPARK_EXECUTOR_MEMORY: "6g"
          large:
            display_name: "Large (8 CPU, 32Gi RAM)"
            kubespawner_override:  # Option-level override
              cpu_guarantee: 4
              cpu_limit: 8
              mem_guarantee: '8G'
              mem_limit: '32G'
              environment:
                SPARK_DRIVER_MEMORY: "24g"
                SPARK_EXECUTOR_MEMORY: "24g"

  - display_name: "GPU Accelerated"
    slug: "gpu"
    description: "GPU-enabled environment for ML/AI"
    profile_options:
      gpu_allocation:
        display_name: "GPU Count"
        choices:
          single:
            display_name: "1 GPU"
            default: true
            kubespawner_override:  # Option-level override
              cpu_guarantee: 4
              cpu_limit: 8
              mem_guarantee: '16G'
              mem_limit: '64G'
              environment:
                SPARK_DRIVER_MEMORY: "48g"
                SPARK_EXECUTOR_MEMORY: "48g"
              extra_resource_guarantees:
                nvidia.com/gpu: '1'
              extra_resource_limits:
                nvidia.com/gpu: '1'
          multi:
            display_name: "2 GPUs"
            kubespawner_override:  # Option-level override
              cpu_guarantee: 8
              cpu_limit: 16
              mem_guarantee: '32G'
              mem_limit: '128G'
              environment:
                SPARK_DRIVER_MEMORY: "96g"
                SPARK_EXECUTOR_MEMORY: "96g"
              extra_resource_guarantees:
                nvidia.com/gpu: '2'
              extra_resource_limits:
                nvidia.com/gpu: '2'
```

**Key Points:**

- **Profile-level `kubespawner_override`**: Use when a profile has fixed resources (no size options). All spawned notebooks from this profile get the same resources.
- **Profile_option-level `kubespawner_override`**: Use when users should choose from multiple resource configurations. The `kubespawner_override` within each choice determines the resources.
- **Mixing approaches**: You can have some profiles with fixed resources (profile-level override) and others with size options (profile_option-level override) in the same configuration.

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

### Developing Grafana Dashboards, Alerts, and Contact Points

All Grafana resources (dashboards, alerts, contact points, datasources, notification policies, etc.) are templated to support dynamic configuration. **Important**: Templates use custom Jinja delimiters `[% %]` for Ansible variables to avoid conflicts with Grafana's internal variable syntax using `{{ }}`.

**Variable syntax**:
- `[% variable %]` - Ansible variables (namespace names, configuration values, etc.)
- `{{ grafana_variable }}` - Grafana internal variables (PromQL label interpolation, alert labels, alert values, etc.)

When creating or modifying Grafana resources, use the appropriate syntax for each context.
**Example**:
```json
{
  "expr": "up{namespace=\"[% temporal_namespace %]\"}", // Ansible variable for namespace
  "legendFormat": "{{ pod }}", // Grafana variable for pod name
  "summary": "Alert on {{ $labels.instance }}" // Grafana variable for alert instance
}
```

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
├── filter_plugins/              # Custom Jinja2 filters
│   └── jvm_memory.py            # JVM to K8s memory conversion
├── tests/unit/filter_plugins/   # Unit tests for filter plugins
│   └── test_jvm_memory.py       # Unit tests for jvm_memory filter
├── group_vars/all/
│   └── versions.yaml            # Centralized version management
├── inventory.example.yaml       # Example inventory (copy to inventory.yaml)
├── playbooks/                   # Ansible playbooks
│   ├── main.yaml                # Main orchestration playbook
│   ├── k3s.yaml                 # K3s installation
│   ├── lake.yaml                # Data lake (MinIO + Hive)
│   ├── analytics.yaml           # Analytics (Trino + Superset)
│   ├── orchestrator.yaml        # Temporal workflow engine
│   └── ...                      # Additional service playbooks
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
│   ├── open-webui/              # Open WebUI + Ollama
│   ├── prometheus/              # Prometheus monitoring
│   ├── loki/                    # Loki log aggregation
│   ├── grafana/                 # Grafana dashboards
│   └── ...                      # Additional roles
├── Makefile                     # Convenience targets
└── README.md                    # This file
```

## Additional Resources

- [Ansible Documentation](https://docs.ansible.com/)
- [K3s Documentation](https://docs.k3s.io/)

## License

See the main Scout repository for license information.
