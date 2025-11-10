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

Modern Kubernetes platforms provide dynamic volume provisioning as a standard feature, eliminating the need for manual PV creation while providing proper node affinity automatically.

## Decision

**We will migrate Scout from static PV provisioning to dynamic provisioning using platform-native storage classes.**

This means:
- Remove all static PV and StorageClass creation code (~220 lines)
- Remove all directory creation tasks for persistent volumes
- Add a single `storage_class` variable that can be set per platform or left empty to use the cluster default
- Use `omit` in Helm templates when `storage_class` is empty (per Kubernetes best practice: omitted field uses default, empty string disables provisioning)
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

**Decision**: Reject for initial implementation. Can be added later by simply changing the `storage_class` variable to a Longhorn storage class if needed.

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
- All static PV code will be removed in one clean step
- Existing deployments must back up data, redeploy with new code, and restore data
- Manual data backup/restore required to migrate existing clusters (out of scope for this ADR)

**Migration approach for existing deployments:**
1. Back up data from existing PVs
2. Deploy Scout using new dynamic provisioning approach
3. Restore data to new dynamically-provisioned PVs

## Support for Multiple Storage Classes (I/O Isolation)

While the primary approach uses a single default storage class per platform, Scout will support **optional configuration of multiple storage classes** for on-premise deployments requiring I/O isolation.

### Use Case

On-premise deployments with multiple physical disks may experience I/O contention when all services share a single storage location:

- Database writes compete with object storage operations
- Monitoring log ingestion causes iowait spikes affecting database performance
- Bulk data processing interferes with real-time query workloads

Multiple storage classes allow operators to distribute workloads across separate physical disks, isolating I/O-intensive services from each other.

### Design

Scout will support an **optional inventory variable** that defines custom storage classes:

```yaml
# Default (most deployments): empty list
storage_classes_to_create: []

# On-premise multi-disk deployments: define custom classes
storage_classes_to_create:
  - name: "local-database"
    path: "/mnt/disk1/k3s-storage"
  - name: "local-objectstorage"
    path: "/mnt/disk2/k3s-storage"
  - name: "local-monitoring"
    path: "/mnt/disk3/k3s-storage"
```

Each storage class definition maps a name to a filesystem path. The path applies uniformly to all nodes in the cluster (per-node path customization is not supported).

### Per-Service Storage Class Assignment

Each Scout service will have a storage class variable that defaults to empty string:

```yaml
# Defaults (use cluster default storage class)
postgres_storage_class: ""
minio_storage_class: ""
prometheus_storage_class: ""

# On-premise multi-disk override
postgres_storage_class: "local-database"
minio_storage_class: "local-objectstorage"
prometheus_storage_class: "local-monitoring"
```

When a service's storage class variable is empty (`""`), the Helm chart will omit the `storageClassName` field from PersistentVolumeClaim definitions, causing Kubernetes to use the cluster's default storage class.

### Implementation Mechanism (k3s)

For k3s deployments, Ansible will configure the local-path-provisioner using its `storageClassConfigs` feature:

1. Generate a ConfigMap (`local-path-config`) with `storageClassConfigs` entries mapping each custom storage class to its path
2. Create corresponding Kubernetes StorageClass resources
3. The local-path-provisioner automatically reloads configuration changes after a brief delay

Example ConfigMap structure:

```json
{
  "nodePathMap": [],
  "storageClassConfigs": {
    "local-database": {
      "nodePathMap": [
        {
          "node": "DEFAULT_PATH_FOR_NON_LISTED_NODES",
          "paths": ["/mnt/disk1/k3s-storage"]
        }
      ]
    },
    "local-objectstorage": {
      "nodePathMap": [
        {
          "node": "DEFAULT_PATH_FOR_NON_LISTED_NODES",
          "paths": ["/mnt/disk2/k3s-storage"]
        }
      ]
    }
  }
}
```

When `storage_classes_to_create` is empty (default), Ansible skips ConfigMap modification and StorageClass creation entirely.

### Platform Portability

This design maintains full portability across platforms:

**Cloud deployments (AWS EKS, GKE, AKS):**
- `storage_classes_to_create: []` (default)
- All service storage class variables: `""` (empty)
- PVCs omit `storageClassName`, use platform default (gp3, pd-ssd, managed-csi)
- Zero configuration required

**On-premise single-disk k3s:**
- `storage_classes_to_create: []` (default)
- All service storage class variables: `""` (empty)
- PVCs use k3s default `local-path` storage class
- All volumes created under `/var/lib/rancher/k3s/storage/`

**On-premise multi-disk k3s:**
- `storage_classes_to_create: [...]` (explicitly configured)
- Service storage class variables assigned to workload types
- PVCs reference specific storage classes
- Volumes distributed across configured disk paths

### Benefits

**Compared to single default storage class:**
- Eliminates I/O contention between database, object storage, and monitoring workloads
- Enables performance tuning (NVMe for databases, HDD for bulk storage)
- Provides capacity management per workload type

**Compared to static PVs:**
- Still fully dynamic: provisioner handles node affinity and PV creation
- Simplified configuration: 3-5 storage classes vs 10+ static PVs
- Automatic scaling: new PVCs provision to appropriate disk without manual intervention

**Maintains portability:**
- Cloud deployments ignore custom storage classes (use platform defaults)
- Single codebase works across all platforms
- Opt-in complexity only for deployments that require I/O isolation

### Limitations

- **k3s-specific**: Multiple storage class configuration only applies to k3s deployments using local-path-provisioner
- **Cloud deployments**: Custom storage classes are ignored; cloud platforms already provide I/O isolation through managed block storage
- **Uniform paths**: Each storage class uses the same path on all nodes (no per-node customization)

### When to Use Multiple Storage Classes

**Use custom storage classes when:**
- On-premise deployment with multiple physical disks
- Observing I/O contention or high iowait times
- Performance-critical databases require isolation from bulk storage operations
- Different storage tiers needed (NVMe, SSD, HDD)

**Use default storage class when:**
- Cloud deployment (block storage already isolated per volume)
- On-premise single-disk deployment
- Small-scale deployment where I/O contention is not a concern
- Development or testing environments

## Platform-Specific Configuration

### k3s (Local Development, On-Premise)
```yaml
# Use cluster default (local-path)
storage_class: ""

# Or explicitly set
storage_class: "local-path"
```

**Provisioner**: Rancher local-path-provisioner (built-in)
**Backend**: Local directories on node filesystem
**Node Affinity**: Automatic (volumes bound to node where first mounted)

### AWS EKS (Cloud Production)
```yaml
storage_class: "gp3"
```

**Provisioner**: EBS CSI driver (requires addon installation)
**Backend**: AWS EBS volumes (gp3 SSD)
**Node Affinity**: Automatic (EBS volumes attached to correct EC2 instance)

**Prerequisite**: EBS CSI driver addon must be enabled on EKS cluster

### Google GKE (Cloud Production)
```yaml
# Use cluster default
storage_class: ""

# Or explicitly set
storage_class: "standard-rwo"  # Or "premium-rwo" for SSD
```

**Provisioner**: GCE PD CSI driver (built-in)
**Backend**: Google Persistent Disks

### Azure AKS (Cloud Production)
```yaml
# Use cluster default
storage_class: ""

# Or explicitly set
storage_class: "managed-csi"
```

**Provisioner**: Azure Disk CSI driver (built-in)
**Backend**: Azure Managed Disks

## Persistent Volume Reclaim Policies

StorageClasses define a `reclaimPolicy` that determines what happens to a PersistentVolume when its associated PersistentVolumeClaim is deleted. All platform-native storage classes used by Scout default to the `Delete` policy.

### Reclaim Policy Options

- **`Delete`** (default): PV and underlying storage are automatically deleted when PVC is deleted
- **`Retain`**: PV remains after PVC deletion and must be manually cleaned up

### Platform Defaults

All platform-native storage classes use `Delete` as the default reclaim policy:

- **k3s local-path**: `Delete`
- **AWS EBS CSI** (`gp3`, `gp2`): `Delete`
- **GKE** (`standard-rwo`, `premium-rwo`): `Delete`
- **AKS** (`managed-csi`): `Delete`

This default is appropriate for Scout because:
- **Development**: Deleting a PVC cleanly removes all associated data (useful for reinstalls)
- **Production**: Data protection relies on backup/restore procedures, not PV retention

### Custom StorageClass (Optional)

If different reclaim behavior is needed, create a custom StorageClass:

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: scout-storage-retain
provisioner: rancher.io/local-path  # Use platform provisioner
reclaimPolicy: Retain  # Override default
volumeBindingMode: WaitForFirstConsumer
```

Then set `storage_class: "scout-storage-retain"` in inventory. However, this is not recommended as it requires manual PV cleanup and does not replace proper backup procedures.

## References

- Kubernetes documentation: [Dynamic Volume Provisioning](https://kubernetes.io/docs/concepts/storage/dynamic-provisioning/)
- k3s storage documentation: [k3s.io/storage](https://docs.k3s.io/storage)
- Rancher local-path provisioner: [github.com/rancher/local-path-provisioner](https://github.com/rancher/local-path-provisioner)
- AWS EBS CSI driver: [AWS EKS User Guide](https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html)
- GKE persistent volumes: [Google Cloud Storage Documentation](https://cloud.google.com/kubernetes-engine/docs/concepts/persistent-volumes)
- AKS storage: [Azure AKS Storage Documentation](https://learn.microsoft.com/en-us/azure/aks/concepts-storage)
