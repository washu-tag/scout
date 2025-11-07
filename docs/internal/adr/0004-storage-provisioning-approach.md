# ADR 0004: Storage Provisioning Approach

**Status**: Proposed
**Date**: 2025-11-07
**Decision Owner/Deciders**: TAG Team

## Context

Scout currently uses static persistent volume (PV) provisioning for all stateful services. This approach requires approximately 220 lines of Ansible code to:

1. Create directories on cluster nodes for each service (PostgreSQL, MinIO, Cassandra, Elasticsearch, Jupyter, Prometheus, Loki, Grafana, Orthanc, DCM4chee)
2. Define static StorageClass resources with `kubernetes.io/no-provisioner`
3. Create PersistentVolume resources using **`hostPath`** volume type (for most services)
4. Map each PV to a specific directory path on a node

**Current Implementation Details:**

The storage setup logic in `ansible/roles/scout_common/tasks/storage_setup.yaml` creates PVs with different volume types:

- **Most services** (PostgreSQL, Cassandra, Elasticsearch, Prometheus, Loki, Grafana, Jupyter, Orthanc, DCM4chee): Use `hostPath` PVs **without node affinity**
- **MinIO only**: Uses `local` PVs with proper node affinity configuration (because MinIO requires multi-node distribution)

This creates a **critical architectural issue**: `hostPath` PVs without node affinity can cause **data loss** if pods restart on a different node, because:
1. The pod scheduler has no information about where the data actually lives
2. If a pod is rescheduled to a different node, it will look for data at the same path on the new node (where it doesn't exist)
3. The pod will start with an empty directory, losing access to existing data

The current approach works reliably **only on single-node clusters** where pod rescheduling can't move pods to different nodes. In multi-node deployments, services could lose data on pod restart.

**Operational Challenges:**

- **Data loss risk**: `hostPath` PVs without node affinity are unsafe in multi-node clusters
- **Platform-specific code**: Different provisioning logic for k3s vs AWS EKS deployments
- **Cognitive burden**: Operators must understand and manage the mapping between services, PVs, nodes, and directory paths
- **Limited portability**: Deployment playbooks include platform-specific logic that makes it harder to deploy Scout on different Kubernetes distributions (GKE, AKS, RKE, etc.)
- **Directory creation overhead**: Two-play playbook structure required for each service (first play creates directories on nodes, second play deploys to Kubernetes)
- **Maintenance complexity**: ~220 lines of Ansible code to create directories, PVs, and storage classes

As documented in `docs/internal/persistent-volume-research.md`, modern Kubernetes platforms provide dynamic volume provisioning as a standard feature, eliminating the need for manual PV creation while providing proper node affinity automatically.

## Decision

**We will migrate Scout from static PV provisioning to dynamic provisioning using platform-native storage classes.**

This means:
- Remove all static PV and StorageClass creation code (~220 lines)
- Remove all directory creation tasks for persistent volumes
- Add a single `scout_storage_class` variable that can be set per platform or left empty to use the cluster default
- Rely on platform-native dynamic provisioners (local-path for k3s, EBS CSI for AWS EKS, etc.)
- Let Kubernetes automatically manage node affinity for locally-attached volumes

## Alternatives Considered

### Option 1: Static PV Provisioning (Current Approach)

**Description**: Continue using manually-created `hostPath` PVs without node affinity (current implementation for most services) or `local` PVs with node affinity (MinIO only).

**Advantages**:
- Full control over directory path organization (`/scout/data/*`, `/scout/persistence/*`, `/scout/monitoring/*`)
- Explicit visibility of data placement on nodes
- No dependency on platform-specific provisioners
- Works well for single-node clusters

**Disadvantages**:
- **Data loss risk in multi-node clusters**: `hostPath` PVs without node affinity can cause pods to lose data if rescheduled to different nodes
- **Not production-ready for multi-node deployments**: Current implementation violates Kubernetes best practices for persistent storage
- ~220 lines of complex Ansible code to maintain
- Inconsistent PV types (MinIO uses `local` with node affinity, everything else uses `hostPath` without)
- Two-play playbook structure required (directory creation + deployment)
- Platform-specific logic (k3s vs AWS conditional paths)
- Difficult to port to new Kubernetes platforms
- Operator must understand PV/PVC/node mapping
- `hostPath` is documented as not recommended for production use in Kubernetes documentation

### Option 2: Dynamic Provisioning with Platform-Native Storage Classes (Recommended)

**Description**: Use Kubernetes dynamic volume provisioning with each platform's native storage class.

**Advantages**:
- **Eliminates ~220 lines of Ansible code** (storage_setup.yaml, storage_dir.yaml, service storage tasks)
- **Automatic node affinity management** - provisioners handle scheduling automatically
- **Zero manual PV/directory creation** - volumes created on-demand when PVCs are created
- **Cross-platform portability** - same Ansible code works on k3s, AWS EKS, GKE, AKS, RKE
- **Industry-standard Kubernetes pattern** - follows best practices documented in Kubernetes storage guides
- **Simpler deployment flow** - single-play playbooks (no directory setup required)
- **Automatic scaling** - new PVCs automatically get provisioned PVs

**Platform Support**:
- k3s: `local-path` provisioner (built-in, no setup required)
- AWS EKS: `gp3` via EBS CSI driver (requires addon installation)
- GKE: `standard-rwo` or `premium-rwo` (built-in)
- AKS: `managed-csi` (built-in)
- RKE/RKE2: `local-path` (built-in)

**Disadvantages**:
- Loss of custom directory path organization (paths chosen by provisioner)
- Slightly less explicit control over exact data placement (though node affinity is still enforced automatically for local volumes)

### Option 3: Dynamic Provisioning with Unified Storage Layer (Longhorn/Rook)

**Description**: Deploy a unified storage layer (Longhorn or Rook/Ceph) to provide consistent storage across all platforms.

**Advantages**:
- Consistent storage behavior across all platforms
- Advanced features: replication, snapshots, migration
- Platform-independent (works identically on k3s, EKS, GKE, etc.)

**Disadvantages**:
- Additional operational complexity (another system to deploy and manage)
- Resource overhead (Longhorn/Rook daemons on each node)
- Unnecessary for single-node or simple deployments
- Platform-native provisioners already provide needed functionality
- Adds dependency that may not be required for Scout's use case

**Decision**: Reject for initial implementation. Can be added later by simply changing the `scout_storage_class` variable to a Longhorn storage class if needed.

## Comparison Matrix

| Criteria | Static PV (Current) | Dynamic (Platform-Native) | Dynamic (Longhorn) |
|----------|---------------------|---------------------------|-------------------|
| **Ansible Code Lines** | ~220 lines | ~0 lines | ~50 lines (deploy Longhorn) |
| **Manual Directory Creation** | Required (every service) | Not required | Not required |
| **Manual PV Creation** | Required (every service) | Not required | Not required |
| **Node Affinity Management** | Manual configuration | Automatic | Automatic |
| **Portability (k3s/EKS/GKE/AKS)** | Platform-specific code | Single codebase | Single codebase |
| **Custom Path Control** | Full control | Provisioner-managed | Provisioner-managed |
| **Operational Complexity** | High | Low | Medium |
| **Deployment Steps** | 2-play structure | 1-play structure | 1-play + Longhorn setup |
| **Industry Standard Pattern** | Legacy approach | Standard practice | Advanced (optional) |

## Rationale

The decision to use dynamic provisioning with platform-native storage classes is based on:

1. **Significant code reduction**: Eliminating ~220 lines of Ansible code reduces maintenance burden and cognitive complexity
2. **Operational simplicity**: Automatic node affinity and PV creation removes error-prone manual configuration
3. **Platform portability**: Scout can deploy to any Kubernetes platform with minimal configuration changes (just set `scout_storage_class` variable)
4. **Industry alignment**: Dynamic provisioning is the recommended Kubernetes pattern since v1.6+
5. **Sufficient for Scout's needs**: Platform-native provisioners meet all current requirements (local volumes, persistence, node affinity)
6. **Future flexibility**: Can migrate to Longhorn/Rook later if advanced features (replication, migration) are needed

The primary trade-off (loss of custom directory paths) is acceptable because:
- Scout's current directory organization (`/scout/data/*`, `/scout/persistence/*`, `/scout/monitoring/*`) is primarily for human readability
- Kubernetes tooling (kubectl, k9s) provides service-to-volume mapping regardless of directory paths
- Provisioner-managed paths still provide data persistence and isolation

## Consequences

### Positive

1. **Reduced maintenance burden**: ~220 fewer lines of Ansible code to maintain, test, and document
2. **Simplified deployment process**: Single-play playbooks, no directory setup required
3. **Automatic node scheduling**: No manual node affinity configuration, provisioners handle it correctly
4. **Better portability**: Same Ansible codebase deploys to k3s, AWS EKS, GKE, AKS, RKE
5. **Faster deployment**: Eliminates directory creation play, reduces deployment time
6. **Fewer points of failure**: No manual PV/directory creation tasks that can fail due to permissions or node availability
7. **Standard Kubernetes pattern**: Aligns with industry best practices and documentation
8. **Easier onboarding**: New operators don't need to learn custom PV/directory mapping

### Negative

1. **Loss of custom directory organization**: Data stored in provisioner-managed paths (e.g., `/var/lib/rancher/k3s/storage/pvc-*` on k3s)
2. **Less explicit path visibility**: Operators can't rely on known directory paths, must use `kubectl get pv` to find paths
3. **Breaking change**: Existing deployments with static PVs cannot automatically migrate (manual migration required, out of scope)
4. **Platform provisioner dependency**: Requires functional storage provisioner on each platform (k3s: local-path, AWS: EBS CSI addon)

### Migration Impact

This is a **breaking change** for existing Scout deployments:
- No automatic migration path from static to dynamic PVs
- Existing deployments will continue to work with static PVs until manually migrated
- Fresh deployments will use dynamic provisioning
- Manual data backup/restore required to migrate existing clusters (out of scope for this ADR)

## Platform-Specific Configuration

### k3s (Local Development, On-Premise)
```yaml
# Use cluster default (local-path)
scout_storage_class: ""

# Or explicitly set
scout_storage_class: "local-path"
```

**Provisioner**: Rancher local-path-provisioner (built-in)
**Backend**: Local directories on node filesystem
**Node Affinity**: Automatic (volumes bound to node where first mounted)

### AWS EKS (Cloud Production)
```yaml
scout_storage_class: "gp3"
```

**Provisioner**: EBS CSI driver (requires addon installation)
**Backend**: AWS EBS volumes (gp3 SSD)
**Node Affinity**: Automatic (EBS volumes attached to correct EC2 instance)

**Prerequisite**: EBS CSI driver addon must be enabled on EKS cluster

### Google GKE (Cloud Production)
```yaml
# Use cluster default
scout_storage_class: ""

# Or explicitly set
scout_storage_class: "standard-rwo"  # Or "premium-rwo" for SSD
```

**Provisioner**: GCE PD CSI driver (built-in)
**Backend**: Google Persistent Disks

### Azure AKS (Cloud Production)
```yaml
# Use cluster default
scout_storage_class: ""

# Or explicitly set
scout_storage_class: "managed-csi"
```

**Provisioner**: Azure Disk CSI driver (built-in)
**Backend**: Azure Managed Disks

## References

- Kubernetes documentation: [Dynamic Volume Provisioning](https://kubernetes.io/docs/concepts/storage/dynamic-provisioning/)
- k3s storage documentation: [k3s.io/storage](https://docs.k3s.io/storage)
- Rancher local-path provisioner: [github.com/rancher/local-path-provisioner](https://github.com/rancher/local-path-provisioner)
- AWS EBS CSI driver: [AWS EKS User Guide](https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html)
- GKE persistent volumes: [Google Cloud Storage Documentation](https://cloud.google.com/kubernetes-engine/docs/concepts/persistent-volumes)
- AKS storage: [Azure AKS Storage Documentation](https://learn.microsoft.com/en-us/azure/aks/concepts-storage)
