# MinIO Storage Isolation

## Problem

On demo01, both MinIO volumes on each node are backed by the same physical disk (e.g., `/dev/sdb` on gpu-01, `/dev/sdd` on big-01). When a disk experiences an I/O stall — as observed on gpu-01 where the disk was unresponsive for 30+ seconds — both MinIO drives go offline simultaneously. This causes write quorum failures and intermittent API errors, including 401s on MinIO Console requests that degrade the user experience.

The root cause is that the local-path provisioner creates both PVCs on the same filesystem. With `volumesPerServer: 2` and a single storage class pointing to one disk mount, there is no device-level isolation between the two volumes.

## Current Architecture

MinIO is deployed via the MinIO Operator Tenant Helm chart. The ansible role (`ansible/roles/minio/tasks/deploy.yaml`) creates a **single pool**:

```yaml
pools:
  - servers: <number of minio_hosts>    # typically 2 (big + gpu nodes)
    name: pool-0
    volumesPerServer: <minio_volumes_per_server>  # typically 2
    size: <minio_storage_size>
    storageClassName: <minio_storage_class>
```

With 2 servers × 2 volumes = 4 drives in one erasure set (EC:2). This tolerates 1 drive failure for both reads and writes.

The storage class is assigned via `minio_storage_class` in inventory, which maps to a path defined in `onprem_local_path_multidisk_storage_classes`. The k3s local-path provisioner creates PVs under that path. Since both volumes on a given node use the same storage class, they resolve to the same filesystem — and the same physical disk.

## Constraints

Three layers of coupling create the problem:

1. **MinIO Operator**: `storageClassName` is per-pool. All volumes within a single pool use the same storage class. There is no way to assign different storage classes to different volumes within one pool.

2. **local-path provisioner**: Each storage class maps to a single directory path (per node). All PVCs for that class land in the same directory, which maps to one disk.

3. **Erasure coding**: Each MinIO pool has independent erasure coding. MinIO distributed mode requires a minimum of 4 drives per pool for meaningful redundancy (EC:2 = tolerates 1 failure). With only 2 drives per pool (EC:1), a single drive failure causes write failures for that pool.

The combined effect: within a single pool, you cannot spread volumes across disks; across multiple pools, you sacrifice per-pool redundancy unless you have enough nodes.

## Potential Solutions

### 1. Expand nodes, reduce volumes per node

**Current**: 2 nodes × 2 vol/node = 4 drives, 1 pool, EC:2
**Proposed**: 4 nodes × 1 vol/node = 4 drives, 1 pool, EC:2

Same erasure coding, same total capacity. Each drive is on a different node, so a single disk failure takes out 1 drive instead of 2. The pool tolerates it.

Currently `minio_hosts` includes only `big` and `gpu` nodes. Adding the `control` node (and potentially one more) would reach 4 nodes. The ansible role already has `tolerations` for the control-plane taint and `podAntiAffinity` by hostname, so this works with zero code changes — inventory only:

```yaml
minio_hosts:
  children:
    server:       # control node (added)
    workers:      # existing big node
    gpu_workers:  # existing gpu node
    # + one more node if available
```

**Pros**: Simplest change. No ansible modifications. Full EC:2 redundancy in a single pool. Each drive on a separate node gives maximum isolation (assuming distinct physical disks per node).

**Cons**: Requires enough nodes (minimum 4 for EC:2 with 1 vol/node). MinIO resource consumption spreads to more nodes. The assumption that different nodes use different physical disks must be verified at the hypervisor level — VMs on the same host with the same backing storage would not improve isolation.

**Variants**:
- 3 nodes × 2 vol = 6 drives (EC:3, tolerates 2 failures, but intra-node disk coupling persists)
- 4 nodes × 2 vol = 8 drives (EC:4, excellent redundancy, but same intra-node coupling)
- These variants increase fault tolerance at the pool level but don't eliminate the original problem of co-located volumes

### 2. Multiple pools with different storage classes

Split into pools that each use a different storage class pointing to a different disk.

The ansible role would need a `minio_pools` list variable. When set, it replaces the current single-pool generation:

```yaml
onprem_local_path_multidisk_storage_classes:
  - name: "minio-disk1"
    path: "/mnt/disk1/scout-data"
  - name: "minio-disk2"
    path: "/mnt/disk2/scout-data"

minio_pools:
  - name: pool-0
    servers: 2
    volumesPerServer: 1
    size: 1900Gi
    storageClassName: "minio-disk1"
  - name: pool-1
    servers: 2
    volumesPerServer: 1
    size: 1900Gi
    storageClassName: "minio-disk2"
```

Changes required:
- `ansible/roles/minio/defaults/main.yaml` — add `minio_pools: []` default
- `ansible/roles/minio/tasks/deploy.yaml` — conditional pool generation (use `minio_pools` when set, fall back to current single-pool logic)
- `ansible/inventory.example.yaml` — document the option

**Pros**: Disk isolation between pools. Each pool's volumes are on a different physical disk.

**Cons**: With 2 servers per pool, each pool has only 2 drives — EC:1 with zero write tolerance for a single drive failure. A node failure also hits both pools (one drive each), which could cascade. This only clearly improves matters if disk failures are more likely than node failures.

**Better with more nodes**: With 4+ nodes and 2 pools, each pool gets 4 drives (EC:2). That provides both disk isolation between pools and meaningful per-pool redundancy. But this requires 4 nodes with 2 physical disks each.

### 3. Custom local-path-provisioner setup script

The local-path provisioner runs a configurable `setup` script when creating each PV. MinIO StatefulSet PVC names follow a deterministic pattern: `data0-scout-pool-0-0`, `data1-scout-pool-0-0`, etc., where the leading number is the volume index within a server.

A custom setup script could parse the volume index from `$PVC_NAME` and create `$VOL_DIR` as a symlink to a disk-specific directory:

```sh
#!/bin/sh
set -eu
VOL_IDX=$(echo "$PVC_NAME" | sed -n 's/^data\([0-9]*\)-.*/\1/p')
if [ "$VOL_IDX" = "1" ] && [ -d "/mnt/disk2" ]; then
  TARGET="/mnt/disk2/local-path-provisioner/${PVC_NAMESPACE}_${PVC_NAME}"
  mkdir -m 0777 -p "$TARGET"
  ln -sfn "$TARGET" "$VOL_DIR"
else
  mkdir -m 0777 -p "$VOL_DIR"
fi
```

With a corresponding teardown:

```sh
#!/bin/sh
set -eu
if [ -L "$VOL_DIR" ]; then
  TARGET=$(readlink -f "$VOL_DIR")
  rm -rf "$TARGET"
  rm -f "$VOL_DIR"
else
  rm -rf "$VOL_DIR"
fi
```

This would be injected via `ansible/roles/scout_common/tasks/storage_classes.yaml`, which already manages the `local-path-config` ConfigMap's `setup` and `teardown` scripts.

**Pros**: Single pool, single storage class, full EC:2 with 4 drives — the current architecture is preserved. Volumes are deterministically placed on different disks based on their index. No MinIO Operator or Helm changes needed.

**Cons**: Fragile. Depends on MinIO's PVC naming convention, which could change across operator versions. Symlinks in provisioner paths may confuse operators or tooling. The script would need to be scoped to MinIO PVCs only (other services shouldn't be affected). Requires each MinIO node to have `/mnt/disk2` mounted.

### 4. Topology-aware storage provisioner (TopoLVM)

Replace the local-path provisioner with [TopoLVM](https://github.com/topolvm/topolvm), a CSI driver that provisions LVM logical volumes with topology awareness. Each physical disk is configured as an LVM volume group. TopoLVM's scheduler extension distributes PVCs across volume groups based on available capacity, providing natural disk isolation without symlink hacks.

**Pros**: Proper solution for topology-aware local storage. Deterministic device-to-PV mapping. Capacity-aware scheduling. Works for all services, not just MinIO.

**Cons**: Significant infrastructure change. Requires LVM setup on all nodes, a new CSI driver deployment, and reworking the storage class strategy across the entire platform. Likely overkill for this specific problem unless there are broader storage management needs.

### 5. Hypervisor / OS-level mirroring

If the nodes are VMs, solve the problem below Kubernetes entirely:

- **RAID1 / disk mirroring**: Each node's MinIO data disk is mirrored across two physical drives at the VM or OS level. A single physical disk failure doesn't stall I/O — the mirror absorbs it. MinIO never sees the failure.
- **Storage QoS**: If I/O stalls are caused by contention rather than hardware failure, apply I/O throttling or priority policies at the hypervisor.
- **Separate backing storage**: Ensure MinIO's virtual disks are backed by different physical arrays or storage controllers.

**Pros**: Completely transparent to Kubernetes and MinIO. No ansible changes. Addresses the root cause (disk reliability) rather than working around it at the application layer.

**Cons**: Requires hypervisor team cooperation. May not be feasible depending on the storage infrastructure. Doesn't help if the issue is VM-level I/O scheduling rather than physical disk failure.

### 6. MinIO scanner tuning (complementary)

The MinIO background scanner (bitrot detection, lifecycle, etc.) generates baseline disk I/O. The current default is `MINIO_SCANNER_SPEED: slowest`. Setting it to `off` eliminates this source of disk pressure:

```yaml
minio_env_variables:
  - name: MINIO_SCANNER_SPEED
    value: "off"
```

This is a mitigation, not a fix — it reduces the probability of I/O pressure but doesn't provide disk isolation. Worth applying alongside any of the above solutions.

## Comparison

| Approach | Disk isolation | Write fault tolerance | Ansible changes | Infrastructure changes |
|---|---|---|---|---|
| **1. More nodes, 1 vol/node** | Between nodes (verify backing) | EC:2 (1 failure) | Inventory only | Add nodes to MinIO pool |
| **2. Multiple pools** | Between pools | EC:1 per pool (0 write failures) | Role + inventory | Separate disk mounts + storage classes |
| **2b. Multiple pools + 4 nodes** | Between pools | EC:2 per pool (1 failure) | Role + inventory | 4 nodes with 2 disks each |
| **3. Custom setup script** | Within node | EC:2 (1 failure) | storage_classes task | Separate disk mounts on each node |
| **4. TopoLVM** | Within node | EC:2 (1 failure) | New role, rework storage | LVM setup on all nodes |
| **5. Hypervisor mirroring** | At hardware level | EC:2 (1 failure) | None | Hypervisor/storage config |

## Recommendation

**Option 1 (expand nodes)** offers the best effort-to-impact ratio. It requires only inventory changes, preserves full EC:2 redundancy in a single pool, and eliminates the intra-node disk coupling entirely — provided different nodes are backed by different physical disks.

If node expansion isn't feasible and intra-node disk isolation is required, **Option 3 (custom setup script)** is the most targeted approach that preserves the single-pool architecture and full erasure coding. It should be treated as a workaround with clear documentation of its fragility.

If hypervisor control is available, **Option 5** is the most robust and invisible to the application layer.

**Option 6 (scanner tuning)** should be evaluated as a complement to any solution.
