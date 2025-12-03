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

**Pin Jupyter single-user pods to GPU nodes when available** using pod nodeSelector. For non-GPU clusters, rely on automatic PV node affinity for pod placement consistency.

### Implementation Details

**Node Selection Logic**:
- Check if `gpu_workers` Ansible group exists and has nodes: set `nodeSelector` to first GPU worker hostname
- Otherwise: no `nodeSelector` (Kubernetes scheduler places pod freely)

**Storage Provisioning** (k3s local-path-provisioner with `volumeBindingMode: WaitForFirstConsumer`):
1. Pod scheduled to node (GPU node if `nodeSelector` set, any available node otherwise)
2. PVC binding delayed until pod placement confirmed
3. Provisioner creates PV on pod's scheduled node
4. Provisioner automatically sets `VolumeNodeAffinity` on PV matching that node
5. Subsequent pod launches bound to same node via PV's node affinity

**Behavior**:
- GPU clusters: Pods pinned to GPU node via explicit `nodeSelector`, ensuring GPU resource access
- Non-GPU clusters: First pod lands on any worker node, PV created there, subsequent pods naturally pinned via PV node affinity
- Single-node clusters: Works without `nodeSelector`

## Rationale

**Why local storage?** SQLite locking is architecturally incompatible with network filesystems. Local storage eliminates corruption issues and is simpler than network mounts with workarounds.

**Why pin GPU clusters but not CPU-only clusters?** GPU resource access requires explicit node selection. For CPU-only clusters, PV node affinity (automatically set by local-path provisioner) provides pod placement consistency without explicit pinning.

**Why use WaitForFirstConsumer binding mode?** Delays PV creation until pod placement is determined, allowing provisioner to create volumes on the correct node. Combined with automatic VolumeNodeAffinity, ensures subsequent pods land on the same node as the first pod.

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

1. **Not HA**: Node failure makes Jupyter unavailable until node recovery
2. **Single point of failure**: All users' notebook data stored on one node's local storage
3. **Limited portability**: Cannot move user data to different node without backup/restore

### Mitigation Strategies

Node-level redundancy, automated backups to MinIO, idle culler, Prometheus/Grafana monitoring. Multi-GPU clusters: distribute users across GPU nodes with consistent hashing (future enhancement).

## Migration Path

1. Backup user data: `kubectl cp jupyter/<pod>:/home/jovyan/<user> ./backup/`
2. Delete existing PVC and static PV (or rename)
3. Deploy updated Jupyter role with node pinning
4. Restore data: `kubectl cp ./backup/<user> jupyter/<pod>:/home/jovyan/`

## References

- ADR 0004: Storage Provisioning Approach (superseded for Jupyter)
- SQLite network filesystem issues: https://www.sqlite.org/lockingv3.html
- K3s local-path provisioner: https://github.com/rancher/local-path-provisioner
- Kubernetes WaitForFirstConsumer: https://kubernetes.io/docs/concepts/storage/storage-classes/#volume-binding-mode
