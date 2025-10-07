# Ansible Roles Structure

## Overview

Scout's Ansible deployment is transitioning from a playbook-based architecture to a role-based architecture. **This is a work in progress**: currently only the `postgres` playbook has been converted to use roles (`scout_common` and `postgres`). The remaining playbooks still use the traditional task-based approach.

Roles provide reusable, testable components with clear separation of concerns and centralized configuration management. Once complete, this transition will make the deployment more maintainable and easier to test.

**Note on Ansible Galaxy**: Scout roles are **not published to Ansible Galaxy**. The `meta/main.yaml` files contain only minimal metadata (role_name and namespace) required to satisfy Molecule validation. They are not intended for Galaxy distribution.

## Directory Structure

```
ansible/
├── roles/
│   ├── scout_common/                 # Shared tasks for all services
│   │   ├── defaults/main.yaml        # Common constants
│   │   ├── meta/main.yaml
│   │   └── tasks/
│   │       ├── namespace.yaml        # Create Kubernetes namespaces
│   │       ├── storage_dir.yaml      # Create host storage directories
│   │       └── storage_setup.yaml    # Configure K8s PV/StorageClass
│   └── postgres/                     # Service-specific role
│       ├── defaults/main.yaml        # Dev-friendly defaults
│       ├── meta/main.yaml            # Role dependencies
│       ├── tasks/
│       │   ├── main.yaml             # Entry point
│       │   ├── storage.yaml          # Node storage setup
│       │   └── deploy.yaml           # Kubernetes deployment
│       ├── templates/                # Jinja2 templates
│       └── molecule/                 # Molecule tests
├── inventory*.yaml                   # Host definitions and configuration
└── playbooks/
    └── postgres.yaml                 # Minimal playbook using role
```

## Configuration Hierarchy

Variables are resolved in order of precedence (lowest to highest):

1. **Role defaults** (`roles/<service>/defaults/main.yaml`)
   - Reasonable defaults suitable for development
   - Example: `postgres_storage_size: 1Gi`

2. **Inventory vars** (`inventory*.yaml` in `k3s_cluster.vars`)
   - Environment-specific configuration and overrides
   - Secrets (vault-encrypted)
   - Example: `postgres_storage_size: 500Gi`

3. **Extra vars** (`-e` command line flag)
   - Runtime overrides
   - Example: `ansible-playbook -e postgres_storage_size=200Gi ...`

## Existing Roles

### scout_common

Shared role providing common tasks used by multiple services:

- **namespace.yaml**: Creates Kubernetes namespaces
- **storage_dir.yaml**: Creates directories on cluster nodes for persistent storage
- **storage_setup.yaml**: Configures Kubernetes PersistentVolumes and StorageClasses

Used by: postgres, lake (minio, hive), orchestrator (temporal), monitoring, jupyter, orthanc, dcm4chee

### postgres

Deploys PostgreSQL cluster with Zalando operator:

- **storage.yaml**: Creates host directories on k3s nodes
- **deploy.yaml**: Deploys PostgreSQL operator and cluster via Kubernetes resources
- **templates/postgres_init/**: SQL initialization scripts for databases

Features:
- Automatic database initialization for Hive Metastore, Superset, HL7 ingestion
- Configurable resources, parameters, and storage
- Molecule tests for role validation

### harbor

Deploys Harbor container registry as a proxy cache for air-gapped deployments:

- **storage.yaml**: Creates host directories on staging node
- **deploy.yaml**: Deploys Harbor via Helm chart, configures proxy cache projects

Features:
- Self-signed TLS certificate generation with Subject Alternative Names (SANs)
- Docker Hub and GHCR proxy cache projects
- API-based configuration of registry endpoints and proxy projects
- Two exposure modes:
  - **ingress** (default): Traefik ingress at domain root for production
  - **nodePort**: NodePort 30443 for development or shared domains
- external_url support for Tailscale/cross-network access
- Automatic Harbor readiness checks
- Idempotent deployment (handles already-exists scenarios)
- Molecule tests for role validation

Configuration:
- `harbor_expose_type`: Set to 'ingress' or 'nodePort'
- `external_url`: Optional Tailscale hostname or DNS name (overrides inventory_hostname)
- `harbor_admin_password`: Admin password (vault-encrypted in inventory)

Used by: Staging node deployment when `use_staging_node: true`

## Planned Role Refactoring

The following playbooks will be refactored into roles in future work:

- **lake**: MinIO object storage and Hive Metastore deployment
- **orchestrator**: Temporal workflow engine with Cassandra backend
- **extractor**: HL7 data extraction services (hl7log-extractor, hl7-transformer)
- **analytics**: Apache Superset for data visualization
- **monitoring**: Prometheus, Grafana, and Loki for cluster monitoring
- **jupyter**: JupyterHub for interactive data analysis

Each role will follow the same structure as `postgres`, with:
- Dev-friendly defaults in `defaults/main.yaml`
- Service-specific deployment logic in `tasks/`
- Dependency on `scout_common` for shared functionality
- Optional Molecule tests for validation

## Writing Playbooks

Playbooks using roles are concise and declarative:

```yaml
---
- name: Create storage directories for Postgres
  hosts: k3s_cluster
  gather_facts: false
  tasks:
    - name: Create storage directories
      ansible.builtin.include_role:
        name: postgres
        tasks_from: storage

- name: Deploy Postgres
  hosts: server
  gather_facts: false
  environment:
    KUBECONFIG: '{{ kubeconfig_yaml }}'
    K8S_AUTH_KUBECONFIG: '{{ kubeconfig_yaml }}'
  roles:
    - postgres
```

## Variable Scope and Delegation

**Key principle**: Kubernetes operations run on `hosts: server` (which is in the `k3s_cluster` group) to ensure proper variable resolution from inventory.

- Variables defined in `k3s_cluster.vars` are only accessible to hosts in that group
- Most Kubernetes operations run directly on `server` host without delegation
- Block-level delegation used only when needed (e.g., Helm chart installs from local filesystem)

## Benefits of Role-Based Structure

1. **Reusable**: Roles can be used across multiple environments and projects
2. **Testable**: Molecule framework enables automated role testing
3. **Maintainable**: Logic centralized in roles, not scattered across playbooks
4. **Clear dependencies**: Declared in `meta/main.yaml`
5. **Minimal inventory files**: Only connection params, secrets, and overrides
6. **Self-documenting**: Role structure makes organization obvious

## Testing Roles

Roles can be tested with Molecule:

```bash
cd ansible/roles/postgres
uvx molecule test           # Full test cycle
uvx molecule converge       # Deploy only
uvx molecule verify         # Run verification tests
```

See individual role `molecule/` directories for test configuration.
