# Node Verification

Validates that provisioned nodes meet Scout deployment requirements: mount points, network connectivity, disk sizes, and hardware resources.

## Quick Start

From the `ansible/` directory:

```bash
make verify-node \
  INVENTORY_FILE=/path/to/inventory.yaml \
  VAULT_PASSWORD_ARG="--ask-vault-pass" \
  ADD="-e ansible_user=<your_username>"
```

This runs verification on all nodes in the `remotes` group without requiring sudo.

## Make Options

| Variable | Default | Description |
|---|---|---|
| `INVENTORY_FILE` | `inventory.yaml` | Path to Ansible inventory file |
| `VAULT_PASSWORD_ARG` | `--vault-password-file vault/pwd.sh` | How to provide the vault password |
| `ADD` | _(empty)_ | Additional `ansible-playbook` flags |

### Limiting Scope

```bash
# Single node
make verify-node ADD="-l tagprod-gpu-xl-01"

# Group of nodes
make verify-node ADD="-l gpu_workers"

# Single check category (mounts, connectivity, or resources)
make verify-node ADD="-e verify_node_command=mounts"
```

## Inventory Configuration

Add `verify_node_*` variables to your inventory at the appropriate group level. Put shared checks (like air-gap validation) on `k3s_cluster` and node-specific specs (like GPU count) on individual groups.

### Mounts

```yaml
verify_node_mounts:
  - path: /scout/data
    state: mounted       # or "absent"
    writable: true       # required when state is "mounted"
    min_size_gb: 9000    # optional disk size check
  - path: /beegfs/shared
    state: mounted
    writable: false
  - path: /beegfs/rad
    state: absent
```

### Connectivity

```yaml
verify_node_connectivity:
  timeout_seconds: 5
  checks:
    - description: "SMTP relay"
      host: osmtp.wustl.edu
      port: 25
      expect: reachable    # or "unreachable" for air-gap validation
    - description: "Internet (air-gap)"
      host: 8.8.8.8
      port: 443
      expect: unreachable
  dns:
    - description: "Scout DNS"
      hostname: scout.example.com
      expect: resolvable   # or "unresolvable"
```

### Resources

```yaml
verify_node_resources:
  min_cpu_cores: 32
  min_memory_gb: 256     # 5% tolerance for kernel reservation
  gpus:
    count: 4
    min_vram_gb: 80      # checked via nvidia-smi
```

### Example: Group-Level Organization

```yaml
staging:
  vars:
    # Staging can reach internet, should NOT reach K3s API
    verify_node_connectivity:
      checks:
        - { host: ghcr.io, port: 443, expect: reachable, description: "Internet" }
        - { host: control-01, port: 6443, expect: unreachable, description: "K3s API" }

k3s_cluster:
  vars:
    # All K3s nodes share the same mounts and connectivity checks
    verify_node_mounts:
      - { path: /scout/data, state: mounted, writable: true, min_size_gb: 9000 }
      - { path: /var/lib/rancher, state: mounted, writable: true, min_size_gb: 900 }
    verify_node_connectivity:
      checks:
        - { host: 8.8.8.8, port: 443, expect: unreachable, description: "Air-gap" }

gpu_workers:
  vars:
    # GPU-specific resource requirements
    verify_node_resources:
      min_cpu_cores: 32
      min_memory_gb: 256
      gpus: { count: 4, min_vram_gb: 80 }
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed |
| 1 | One or more checks failed |
| 2 | Configuration or tool error |

## Standalone Usage

The script can also be run directly (without Ansible):

```bash
python3 verify_node.py all --config config.json
python3 verify_node.py mounts --config-json '{"hostname": "node1", ...}'
```

See `config.example.json` for a complete config example.
