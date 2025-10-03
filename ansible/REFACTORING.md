# Ansible Refactoring Documentation

## Overview

This document describes the refactoring of Scout's Ansible deployment from a playbook-based structure to a role-based structure with centralized configuration management.

## What Changed

### Before Refactoring

**Structure:**
```
ansible/
├── inventory.example.yaml (214 lines)
├── playbooks/
│   ├── postgres.yaml (151 lines)
│   ├── lake.yaml
│   ├── extractor.yaml
│   └── ... (45 playbooks total)
```

**Problems:**
- Inventory files mixed connection params, configuration, secrets, and computed values
- 160+ lines of configuration in each inventory file
- Playbooks contained repetitive patterns (namespace creation, storage setup, `delegate_to: localhost`)
- No testing infrastructure for individual components
- Difficult to override configuration per environment

### After Refactoring

**Structure:**
```
ansible/
├── group_vars/
│   ├── all/                          # Available to all hosts
│   │   ├── namespaces.yaml           # Namespace configuration
│   │   ├── computed.yaml             # Computed values (s3_endpoint, etc)
│   │   ├── database.yaml             # Storage paths, table names
│   │   └── container_paths.yaml     # Container mount paths
│   └── k3s_cluster/                  # Available to k3s_cluster group
│       ├── postgres.yaml             # Postgres configuration
│       ├── minio.yaml                # MinIO/S3 configuration
│       ├── monitoring.yaml           # Grafana/Prometheus config
│       ├── jupyter.yaml              # JupyterHub configuration
│       ├── superset.yaml             # Superset configuration
│       ├── orchestrator.yaml         # Temporal configuration
│       ├── extractor.yaml            # HL7 extractor configuration
│       ├── paths.yaml                # Host filesystem paths
│       └── k3s.yaml                  # K3s cluster configuration
├── roles/
│   ├── scout_common/                 # Reusable common tasks
│   │   ├── defaults/main.yaml        # Hardcoded constants
│   │   ├── meta/main.yaml
│   │   └── tasks/
│   │       ├── main.yaml
│   │       ├── namespace.yaml        # Reusable namespace creation
│   │       └── storage_dir.yaml      # Reusable storage dir creation
│   └── postgres/
│       ├── defaults/main.yaml        # Dev-friendly defaults
│       ├── meta/main.yaml            # Depends on scout_common
│       ├── tasks/
│       │   ├── main.yaml             # Entry point
│       │   ├── storage.yaml          # Runs on k3s nodes
│       │   ├── deploy.yaml           # Runs on localhost (k8s API)
│       │   └── storage_setup.yaml    # K8s PV/StorageClass setup
│       └── templates/
│           └── postgres_init/        # SQL init templates
├── inventory.example.yaml (82 lines - 61% reduction)
└── playbooks/
    └── postgres.yaml (20 lines - 87% reduction!)
```

## Key Improvements

### 1. Centralized Configuration

**Before:**
All configuration lived in inventory files, making it hard to:
- See what values were available
- Share configuration between environments
- Override only what's different per environment

**After:**
- `group_vars/all/`: Shared constants and computed values available everywhere
- `group_vars/k3s_cluster/`: Default configuration for all k3s deployments
- `inventory.yaml`: Only hosts, connection params, and environment-specific overrides

**Example - Postgres Configuration:**

```yaml
# group_vars/k3s_cluster/postgres.yaml (shared defaults)
postgres_user: scout
postgres_resources:
  requests:
    cpu: 4
    memory: 64Gi

# inventory.cluster03.yaml (environment-specific override)
postgres_resources: {}  # Use defaults from group_vars
```

### 2. Role-Based Organization

**Benefits:**
- Clear separation of concerns (storage vs deployment)
- Reusable across projects
- Self-contained with dependencies declared in `meta/main.yaml`
- Can be tested independently with Molecule
- Centralized logic in roles instead of scattered across playbooks

### 3. Variable Precedence

Configuration is resolved in this order (lowest to highest priority):

1. **Role defaults** (`roles/postgres/defaults/main.yaml`) - Safe dev defaults
2. **Group vars** (`group_vars/k3s_cluster/postgres.yaml`) - Production defaults
3. **Inventory vars** (`inventory.yaml`) - Environment-specific overrides
4. **Extra vars** (`-e` flag) - Command-line overrides

**Example:**

```yaml
# roles/postgres/defaults/main.yaml
postgres_storage_size: 1Gi  # Dev default

# group_vars/k3s_cluster/postgres.yaml
postgres_storage_size: 100Gi  # Production default (overrides role default)

# inventory.laptop.example.yaml
postgres_storage_size: 1Gi  # Laptop override (overrides group_vars)
```

## Migration Guide

### For Existing Inventory Files

If you have an existing inventory file (like `inventory.cluster03.yaml`):

1. **Keep:**
   - Host definitions (server, workers, gpu_workers groups)
   - Connection parameters (ansible_host, ansible_user, ansible_become, etc)
   - Vault-encrypted secrets

2. **Remove:**
   - Configuration that matches `group_vars/k3s_cluster/*.yaml` defaults
   - Namespace definitions (now in `group_vars/all/namespaces.yaml`)
   - Computed values (now in `group_vars/all/computed.yaml`)
   - Path definitions that match standard paths

3. **Keep but move to k3s_cluster.vars:**
   - Environment-specific overrides (storage sizes, resource limits, etc)
   - Secrets (use ansible-vault)
   - Paths that differ from defaults

**Example Migration:**

**Before (200+ lines):**
```yaml
k3s_cluster:
  vars:
    ansible_user: myuser
    postgres_user: scout
    postgres_password: !vault |...
    postgres_storage_size: 500Gi
    postgres_resources: {...}
    postgres_parameters: {...}
    minio_storage_size: 750Gi
    s3_password: !vault |...
    # ... 180 more lines
```

**After (~50 lines):**
```yaml
k3s_cluster:
  vars:
    # Connection
    ansible_user: myuser

    # Environment-specific overrides (only what's different!)
    postgres_storage_size: 500Gi  # Different from default 100Gi
    minio_storage_size: 750Gi     # Different from default 750Gi

    # Secrets (vault-encrypted)
    postgres_password: !vault |...
    s3_password: !vault |...

    # Everything else comes from group_vars!
```

### Running Playbooks

**No changes needed!** Playbooks run the same way:

```bash
# Still works exactly the same
ansible-playbook -i inventory.cluster03.yaml playbooks/postgres.yaml

# Full deployment
ansible-playbook -i inventory.cluster03.yaml playbooks/main.yaml
```

## Line Count Comparison

### Postgres Component

| File | Before | After | Reduction |
|------|--------|-------|-----------|
| `playbooks/postgres.yaml` | 151 lines | 20 lines | **87%** |
| `inventory.example.yaml` | 214 lines | 82 lines | **62%** |

**Total for postgres:**
- Before: 365 lines (playbook + inventory)
- After: 102 lines (organized, reusable, testable)
- Plus: ~300 lines of role code (reusable across all environments!)
- Plus: ~200 lines of Molecule tests

### Overall Structure

**Before:**
- 45 playbooks with repetitive patterns
- All config in inventory files (200+ lines each for production environments)
- No testing infrastructure
- ~40 `delegate_to: localhost` per playbook

**After:**
- Organized group_vars (80 lines total)
- Slim inventory files (~50-100 lines for production, ~80 for example)
- Reusable roles with tests
- Zero `delegate_to` needed
- Molecule test scenarios for each role

## Benefits

1. **Easier to understand:** Configuration organized by service, not buried in inventory
2. **Easier to maintain:** Change defaults in one place (group_vars), override per environment
3. **Easier to test:** Can add Molecule tests for each role
4. **Easier to extend:** New environments just need minimal inventory + overrides
5. **More maintainable:** DRY principles, reusable tasks, clear dependencies
6. **Production ready:** Clear separation of dev defaults vs production configuration

## Lessons Learned: Variable Scope and Delegation

### The Variable Scope Issue

**Challenge**: Kubernetes operations need to run where the Python kubernetes library is installed (typically localhost), but variables need to be resolved from the k3s_cluster group.

**Solution**: Use `hosts: server` (which is in k3s_cluster group) for variable resolution. When needed, use block-level delegation for operations that require localhost execution.

**Why this matters:**
```yaml
# This doesn't work:
- hosts: localhost
  tasks:
    - debug: msg={{ postgres_cluster_namespace }}  # UNDEFINED!
    # localhost is not in k3s_cluster group, so can't see group_vars/k3s_cluster/*

# This works:
- hosts: server  # server IS in k3s_cluster group
  tasks:
    - debug: msg={{ postgres_cluster_namespace }}  # Works!
    # Can access all group_vars/k3s_cluster/ variables
```

**Current pattern** (already implemented in most playbooks):
- Most K8s operations run directly on `hosts: server` without delegation
- Block-level delegation used only when needed (e.g., Helm chart installs from local filesystem)
- This refactoring maintains this existing pattern

### Environment Variables

**KUBECONFIG**: Required for kubectl/helm operations
- Precedence: `-e kubeconfig_yaml=/path` → `$KUBECONFIG` → `~/.kube/config`
- Cluster-specific, must be explicitly set when not running full main.yaml

**HELM_PLUGINS**: Not needed
- Helm auto-discovers plugins from standard locations
- Removed in refactoring to avoid requiring developer-specific paths in repo

**K8S_AUTH_KUBECONFIG**: Required by kubernetes.core modules
- Should match KUBECONFIG value
- Set in playbook environment block

## Refactoring Status

### Phase 1: Centralized Configuration (COMPLETE)

**What was done:**
- ✅ Created `group_vars/all/` structure (namespaces, computed values, database config, container paths)
- ✅ Created `group_vars/k3s_cluster/` structure (service-specific config organized by service)
- ✅ Reduced `inventory.example.yaml` from 214 to 82 lines (62% reduction)
- ✅ Reduced `inventory.cluster03-ssh.yaml` from 288 to 189 lines (34% reduction)
- ✅ Created `ansible.cfg` for roles_path configuration
- ✅ Moved inventory files into `ansible/` directory (idiomatic structure)
- ✅ Established variable precedence: role defaults → group_vars → inventory vars

**Files to include in PR:**
- `ansible/group_vars/` (entire directory structure)
- `ansible/ansible.cfg`
- `ansible/inventory.example.yaml` (updated)
- `ansible/REFACTORING.md` (this file)
- Updated inventory files moved to `ansible/` directory

**What to exclude from initial PR:**
- Role refactoring (postgres role, scout_common role)
- Playbook changes
- Molecule testing setup

### Phase 2: Postgres Role-Based Refactoring (IN PROGRESS)

**Scope:**
- Refactor postgres deployment only (not other services)
- Create `scout_common` role with shared task files
- Create `postgres` role with postgres-specific logic
- Update only `inventory.example.yaml` and `inventory.cluster03-ssh-roles.yaml`
- Keep other inventory files and playbooks unchanged for now

**Key Correction from Previous Attempt:**
- ❌ Previous: `storage_setup.yaml` was refactored into `roles/postgres/tasks/storage_setup.yaml`
- ✅ Corrected: `storage_setup.yaml` should be in `roles/scout_common/tasks/storage_setup.yaml`
- **Reason**: `storage_setup.yaml` is used by 7 playbooks (postgres, lake, orchestrator, monitor, jupyter, orthanc, dcm4chee), so it belongs in the common role, not postgres-specific role

**Structure:**

1. **scout_common role** - Reusable common tasks:
   ```
   ansible/roles/scout_common/
   ├── defaults/main.yaml        # Minimal or empty defaults
   ├── meta/main.yaml            # Role metadata
   └── tasks/
       ├── main.yaml             # Empty entry point
       ├── namespace.yaml        # Reusable namespace creation
       ├── storage_dir.yaml      # From playbooks/tasks/storage_dir_create.yaml
       └── storage_setup.yaml    # From playbooks/tasks/storage_setup.yaml ✅ CORRECTED
   ```

2. **postgres role** - Postgres-specific logic:
   ```
   ansible/roles/postgres/
   ├── defaults/main.yaml        # Dev-friendly defaults
   ├── meta/main.yaml            # Depends on scout_common
   ├── tasks/
   │   ├── main.yaml             # Entry point - calls deploy.yaml
   │   ├── storage.yaml          # Node-side directory creation
   │   └── deploy.yaml           # K8s deployment tasks
   └── templates/
       └── postgres_init/        # From playbooks/templates/postgres_init/
           ├── README.md
           ├── hive_init_sql.yaml
           └── superset_init_sql.yaml
   ```

3. **Updated postgres playbook** using roles:
   ```yaml
   - name: Create storage directories for Postgres
     hosts: k3s_cluster
     gather_facts: false
     tasks:
       - include_role:
           name: postgres
           tasks_from: storage

   - name: Deploy Postgres
     hosts: server              # Has access to group_vars/k3s_cluster/
     gather_facts: false
     environment:
       KUBECONFIG: '{{ kubeconfig_yaml }}'
     tasks:
       - include_role:
           name: postgres
   ```

**Inventory Changes (LIMITED SCOPE):**
- Only modify `inventory.example.yaml` and `inventory.cluster03-ssh-roles.yaml`
- Only move postgres-specific variables that should become role defaults
- Keep postgres secrets and environment-specific overrides in inventory
- Keep all non-postgres variables unchanged in k3s_cluster.vars

**Variable Naming Conventions:**
- Prefix postgres-specific vars: `postgres_*`
- Use `ns` instead of `namespace` (reserved name)
- Storage vars: `postgres_dir`, `postgres_storage_size`, `postgres_storage_class`

**Implementation Details:**
- Maintain existing delegation patterns (most tasks run on `hosts: server` without delegation)
- Use `KUBECONFIG` environment variable for kubectl/helm operations
- postgres role depends on `scout_common` in `meta/main.yaml` for shared tasks
- Move postgres-specific logic from playbook into role tasks

### Phase 3: Other Services Role-Based Refactoring (TODO - Future)

**Services to refactor** (in order):
- lake (minio + hive)
- orchestrator (temporal)
- extractor (hl7log-extractor + hl7-transformer)
- analytics (superset)
- monitoring (prometheus, grafana, loki)
- jupyter

## Questions?

See this document for the complete context and lessons learned from Phase 1.
