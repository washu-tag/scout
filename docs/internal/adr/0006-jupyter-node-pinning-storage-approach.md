# ADR 0006: Jupyter Node Pinning and Storage Approach

**Date**: 2025-12-01  
**Status**: Proposed  
**Decision Owner**: TAG Team  
**Supersedes**: Jupyter-specific portions of ADR 0004

## Context

ADR 0004 established Scout's migration to dynamic volume provisioning with platform-native storage classes. However, JupyterHub single-user pods required special handling due to a requirement for multi-node GPU scheduling: users should be able to start notebooks on CPU nodes and later restart on GPU nodes to access GPU resources.

To support this, ADR 0004 prescribed ReadWriteMany (RWX) storage using static hostPath PVs pointing to network-mounted directories (NFS/BeeGFS). This approach was implemented and tested successfully on simulated network mounts (local directories mounted into multiple k3d containers).

### Problem: Network Filesystem Incompatibility

Production deployment with NFS-mounted BeeGFS exposed critical SQLite incompatibility. JupyterLab uses SQLite for FileIdManager, YStore, and IPython history. SQLite's file locking fails on network filesystems, causing corruption and startup failures.

Current workarounds use emptyDir volumes for SQLite databases, disable IPython history, and add complexity. This loses data on pod restart and violates best practices.

The original requirement—flexible scheduling across CPU/GPU nodes—is less valuable than reliability. GPU nodes have sufficient CPU resources for both workloads, and users rarely migrate running notebooks between node types.

## Decision

**Pin all Jupyter single-user pods to a specific node** using pod nodeSelector. Prefer GPU node when available, otherwise use CPU worker node. This enables dynamic local storage provisioning with automatic volume node affinity.

### Implementation Details

**Pod Node Scheduling**:
- Set `nodeSelector` on single-user pods with `kubernetes.io/hostname: <target-node>`
- Forces pod to schedule on specific node before storage provisioning
- Local-path provisioner creates PV on same node where pod is scheduled
- Provisioner automatically sets node affinity on created PV

**Node Selection Logic**:
- Check if `gpu_workers` Ansible group exists and has nodes: use first GPU worker hostname
- Otherwise, check if `workers` group exists and has nodes: use first CPU worker hostname
- Otherwise, no nodeSelector (single-node cluster or special configuration)
- Node identified by hostname from Ansible inventory

**Storage Configuration**:
- Use dynamic provisioning via `storage.type: dynamic` in JupyterHub Helm values
- JupyterHub creates PVC; local-path provisioner creates PV automatically
- Provisioner creates PV on node where pod is running (due to nodeSelector)
- Provisioner automatically sets node affinity on PV to match creation node

**Behavior**:
- Clusters with GPU nodes: All Jupyter pods pinned to GPU node via nodeSelector
- Clusters without GPU nodes but with workers: Pods pinned to first worker node
- Single-node clusters: Works without nodeSelector

## Rationale

**Why pin to specific node?** SQLite locking is architecturally incompatible with network filesystems. Local storage is simpler than network mounts with workarounds. GPU nodes have sufficient CPU resources for both workloads. Other Scout components accept single-node deployment for on-premise installations.

**Why pod nodeSelector instead of volume affinity alone?** Local-path provisioner creates PVs on the node where the pod is scheduled. Pod nodeSelector ensures pods schedule to desired node; provisioner then creates PV there with automatic node affinity. Cannot control provisioner's node choice without first controlling pod placement.

**Why dynamic provisioning?** Simpler than static PV creation. Matches pattern used by other Scout services. Local-path provisioner automatically sets node affinity on created PVs. No manual PV/PVC management needed.

## Alternatives Considered

### Alternative 1: Network Filesystem with Workarounds
**Rejected**: Does not solve SQLite incompatibility. Loses data on pod restart. Adds maintenance burden.

### Alternative 2: Replace SQLite with Distributed Database
**Rejected**: JupyterLab/IPython have hardcoded SQLite dependencies. Requires forking. High cost. Still needs RWX for notebooks.

### Alternative 3: Cloud RWX Storage (EFS, Filestore, Azure Files)
**Rejected**: Does not solve on-premise deployment. SQLite issues may persist. Adds platform-specific complexity.

### Alternative 4: Static PV with Local Volume and Node Affinity
**Rejected**: More complex than dynamic provisioning. Requires manual PV/PVC management. Local-path provisioner handles node affinity automatically with pod nodeSelector.

## Consequences

### Positive

1. **Simplicity**: Dynamic provisioning with nodeSelector simpler than static PV management or network mounts
2. **Reliability**: Eliminates SQLite database corruption and pod startup failures
3. **Standard pattern**: Matches other Scout services using dynamic provisioning
4. **Reduced maintenance**: No manual PV/PVC creation or network filesystem dependencies
5. **Performance**: Local storage is faster than network filesystems
6. **Automatic node affinity**: Provisioner handles node affinity without manual configuration

### Negative

1. **Not HA**: GPU node failure makes Jupyter unavailable until node recovery
2. **Resource contention**: CPU and GPU workloads share same node's CPU resources
3. **Single point of failure**: All users' notebook data stored on one node's local storage
4. **Limited portability**: Cannot move user data to different node without backup/restore

### Mitigation Strategies

**Production HA**: Use node-level redundancy, automated backups to MinIO, idle culler, Prometheus/Grafana health monitoring.

**Multi-GPU clusters**: Current implementation uses single GPU node. Future: prefer GPU nodes with fallback, or distribute users with consistent hashing.

## Migration Path

1. Backup user data: `kubectl cp jupyter/<pod>:/home/jovyan/<user> ./backup/`
2. Delete existing PVC and static PV (or rename)
3. Deploy updated Jupyter role with node pinning
4. Restore data: `kubectl cp ./backup/<user> jupyter/<pod>:/home/jovyan/`

## References

- ADR 0004: Storage Provisioning Approach (superseded for Jupyter)
- SQLite network filesystem issues: https://www.sqlite.org/lockingv3.html
