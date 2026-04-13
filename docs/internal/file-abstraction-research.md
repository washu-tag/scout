# File Abstraction Layer: Research Report

**Date**: 2026-04-10
**Status**: Research / Exploration
**Context**: Exploring mechanisms to present files from a legacy application's archive (specifically XNAT on Kubernetes) to compute pods without copying, with support for custom file layouts and copy-on-write escape hatches.

## Problem Statement

A legacy application (XNAT) manages files on a filesystem, likely mounted from a PersistentVolume in Kubernetes. We want to:

1. **Present files to compute pods without copying** — zero-copy or near-zero-copy access to the archive
2. **Support arbitrary file layouts** — organize files by custom criteria (modality, date, query result) rather than the upstream application's native directory structure (XNAT: project/subject/experiment/scan)
3. **Read-only by default, copy-on-write escape hatch** — the archive is never modified, but if a compute application needs to modify files in-place, a writable copy is transparently provided
4. **High concurrency** — many compute jobs running simultaneously, potentially accessing overlapping file sets
5. **Batch-oriented** — latency is acceptable; throughput and correctness matter more

### Key Tradeoff

**Zero-copy vs. custom layout**: These goals are in tension.

- **Zero-copy with native layout**: Mount the archive PV directly into compute pods. No copies, but you're stuck with XNAT's `project/subject/experiment/scan/` hierarchy. Simple and fast.
- **Custom layout with copies**: An init container or staging step copies selected files into the desired arrangement. Full layout flexibility, but data duplication and staging time.
- **Custom layout without copies**: Requires a virtual filesystem layer (FUSE, symlinks, bind mounts, or overlay) that presents a remapped view. This is the sweet spot we're targeting, but no single off-the-shelf tool does exactly this.

---

## Part 1: Filesystem-Level Mechanisms

### 1.1 OverlayFS (Linux Kernel)

OverlayFS layers directories into a merged view with copy-on-write semantics.

```
mount -t overlay overlay \
  -o lowerdir=/archive,upperdir=/scratch,workdir=/work \
  /merged
```

- **lowerdir**: Read-only layer(s) — the XNAT archive
- **upperdir**: Writable layer — captures modifications (CoW)
- **merged**: The unified view applications see

**Strengths**:
- Kernel-native, no FUSE overhead
- True copy-on-write: reads go directly to lower layer, writes copy to upper layer on first modification
- Supports multiple stacked lower layers (up to ~500 since Linux 5.11)
- This is exactly how container runtimes (containerd, CRI-O) work — proven at enormous scale

**Limitations**:
- **Cannot rearrange files** — the merged view is the union of all layers at their original paths. No renaming, filtering, or restructuring.
- **upperdir/workdir must be on the same filesystem** — cannot be NFS or another overlay
- **Requires `CAP_SYS_ADMIN`** — meaning privileged containers in Kubernetes, which is a significant security concern
- **Hardlink breakage** — copy-up creates a new inode, breaking hardlinks

**Verdict**: Solves the CoW problem perfectly but does not address custom layouts. Could be combined with another mechanism (e.g., a symlink layer as the lower layer).

### 1.2 Symlink Farms

Create a directory of symbolic links pointing to files in the archive, arranged however you want.

```bash
mkdir -p /view/ct-scans/2024
ln -s /archive/proj1/subj1/exp1/scan1/file1.dcm /view/ct-scans/2024/file1.dcm
ln -s /archive/proj2/subj3/exp7/scan2/file2.dcm /view/ct-scans/2024/file2.dcm
```

**Strengths**:
- True zero-copy — symlinks are just pointers; reading through them goes directly to the archive
- Full layout flexibility — any directory structure, any file naming
- No special privileges required
- Fast to create (thousands of symlinks per second)
- This is how **Nextflow** (the dominant scientific workflow engine) stages input files — `stageInMode: 'symlink'` is the default

**Limitations**:
- **Both volumes must be mounted in the consumer container** — the symlink target must be resolvable. The archive PV and the symlink directory (emptyDir) must both be mounted in the compute pod.
- **Some applications don't follow symlinks** — rare but possible (e.g., some tools that use `O_NOFOLLOW` or `lstat` instead of `stat`)
- **Read-only** — symlinks give you the same access as the target. If the archive is read-only, so is the view. No CoW.
- **Cannot combine with OverlayFS directly** — an overlayfs lower layer that contains symlinks will copy-up the symlink itself, not the target, on write (the symlink becomes a regular file in the upper layer pointing to... the archive path, which may or may not be mounted in the overlay context)

**Verdict**: The simplest and most practical mechanism for custom layouts. The "both volumes must be mounted" requirement is easy to satisfy in Kubernetes. The lack of CoW can be addressed separately.

### 1.3 Hardlinks

Like symlinks but the link and the target share the same inode. Reading through a hardlink is indistinguishable from reading the original file.

**Strengths**:
- True zero-copy, no indirection overhead
- Applications cannot distinguish a hardlink from the original file
- Don't break if the original path is unmounted (hardlinks are same-filesystem references)

**Limitations**:
- **Must be on the same filesystem** — cannot span volumes or PVCs
- **Cannot link directories** — only regular files
- **The archive must be writable** (to create new hardlinks pointing at its inodes) — contradicts our read-only requirement unless we create hardlinks from within a shared volume that has both the archive and the view directory

**Verdict**: Useful in narrow cases (same filesystem, file-only) but too restrictive for general use.

### 1.4 Bind Mounts (via Mount Namespaces)

Linux mount namespaces (inspired by Plan 9) allow per-process filesystem views. `mount --bind` makes a directory appear at another location.

```bash
unshare -m  # new mount namespace
mount --bind /archive/proj1/subj1/exp1/scan1 /workspace/input/scan1
mount --bind /archive/proj2/subj3/exp7/scan2 /workspace/input/scan2
```

**Strengths**:
- Kernel-level, no FUSE overhead
- Can compose arbitrary layouts by binding specific subdirectories into a target tree
- Per-process isolation via mount namespaces (one pod's mounts don't affect another)

**Limitations**:
- **Requires privileges** — `mount` requires `CAP_SYS_ADMIN`
- **Directory-granularity only** — you bind entire directories, not individual files (bind-mounting a single file is possible but fragile)
- **Static** — the binds are set up once; dynamic changes require remounting
- **Mount propagation** — in Kubernetes, mounts made in an init container don't automatically propagate to the main container unless `mountPropagation: Bidirectional` is set (which itself requires privileges)

**Verdict**: Powerful but the privilege requirement makes this impractical for regular Kubernetes workloads. This is essentially what Kubernetes subPath mounts do under the hood, but the kubelet manages them.

### 1.5 Reflinks (Btrfs, XFS, ZFS)

Copy-on-write at the filesystem level. `cp --reflink=always source dest` creates a new file that shares data blocks with the source. Only modified blocks consume new space.

```bash
# Instant, near-zero-space copy
cp --reflink=always /archive/scan1/file1.dcm /workspace/file1.dcm
```

**Strengths**:
- Near-instant regardless of file size
- The copy is fully writable — perfect for the CoW escape hatch
- Kernel-native, no FUSE overhead
- Supported on Btrfs, XFS (since Linux 4.9, default in RHEL 8+), and ZFS (via `zfs clone`)

**Limitations**:
- **Same filesystem only** — source and destination must be on the same Btrfs/XFS/ZFS volume
- **Requires filesystem support** — the PV's underlying filesystem must be reflink-capable
- **Not a view mechanism** — you create actual copies (just space-efficient ones); there is no "live" projection

**Verdict**: The best mechanism for the CoW escape hatch, **if** the node filesystem supports reflinks. When a compute job declares it needs a writable copy, reflink the required files. The layout can be custom because you're creating new files (that happen to share blocks with the originals).

### 1.6 FUSE (Filesystem in Userspace)

A framework for building custom filesystems as userspace programs. The kernel routes VFS operations through `/dev/fuse` to a userspace daemon.

**Key libraries**:
- **libfuse** (C) — reference implementation
- **go-fuse** (Go) — high-performance, used by Google projects
- **fuser** (Rust) — modern Rust implementation, no C dependencies
- **fusepy** (Python) — rapid prototyping

**Existing FUSE tools relevant to this problem**:

| Tool | What it does | Custom layout? | CoW? |
|------|-------------|---------------|------|
| **bindfs** | Remaps ownership/permissions on a bind mount | No (mirror only) | No |
| **mergerfs** | Merges multiple directories into one view | No (union only) | No |
| **unionfs-fuse** | Union mount with CoW (whiteouts) | No (union only) | Yes |
| **rofs-filtered** | Read-only view with pattern-based filtering | Filter only | No |
| **s3fs-fuse** | Mounts S3/MinIO as POSIX filesystem | No (bucket structure) | No |
| **GeeseFS** | High-performance S3 FUSE mount (MinIO-compatible) | No (bucket structure) | No |
| **JuiceFS** | Full POSIX filesystem over object storage + metadata DB | Via symlinks | Yes |
| **Alluxio FUSE** | Unified namespace across storage systems | Nested mounts | Configurable |
| **CVMFS** | Content-addressed, lazy-loading, read-only | Via catalog | No |

**The gap**: No existing FUSE filesystem implements "arbitrary path remapping from a declarative spec." The closest are Alluxio (which allows mounting different storage paths at different virtual paths) and CVMFS (where a catalog defines the virtual tree over content-addressed objects).

**Custom FUSE approach**: Writing a purpose-built FUSE filesystem for this use case is straightforward:
- Read a mapping file (JSON/YAML) that declares `{virtual_path: real_path}` entries
- Implement `lookup`, `getattr`, `readdir`, `read` by delegating to the real paths
- For CoW: intercept `open` with `O_WRONLY`/`O_RDWR`, copy-on-first-write to a scratch area
- Serve this via a CSI driver or sidecar container in Kubernetes

The per-I/O overhead of FUSE (two context switches per operation) is significant for metadata-heavy workloads (many small files) but acceptable for data-heavy workloads (few large files). DICOM series can go either way — many small files per series or a few large multi-frame files.

**Verdict**: FUSE is the most flexible mechanism. A custom FUSE filesystem is likely the core of any "virtual file view" solution. The question is whether to build one from scratch or adapt an existing tool.

---

## Part 2: Kubernetes-Native Mechanisms

### 2.1 Kubernetes Projected Volumes

Built-in mechanism to combine ConfigMaps, Secrets, Downward API, and ServiceAccountTokens into a single mount with custom path layout.

**Cannot project from PVCs.** Only the four sources above. Size-limited (1 MiB per ConfigMap/Secret). Not applicable to data files.

### 2.2 SubPath Mounts

Mount a subdirectory of a volume at a specific path:

```yaml
volumeMounts:
- name: archive
  mountPath: /workspace/input/scan1
  subPath: project1/subject1/experiment1/scan1
- name: archive
  mountPath: /workspace/input/scan2
  subPath: project2/subject3/experiment7/scan2
```

**Strengths**: No copies, no FUSE, no privileges. Multiple subPaths from the same PVC can be mounted at different paths, creating a custom layout.

**Limitations**:
- **Static** — subPaths are defined in the pod spec at creation time. Cannot be computed dynamically (though `subPathExpr` allows environment variable substitution).
- **No globbing** — must specify exact paths.
- **Cannot flatten** — each subPath mount is a whole directory; you can't cherry-pick individual files into a flat layout.
- **Pod spec explosion** — a job needing 1000 files from different paths would need 1000 volumeMount entries (impractical).

**Verdict**: Works for simple cases (mount a few specific directories) but doesn't scale to arbitrary file selection.

### 2.3 Init Container Patterns

The most common production approach. An init container runs before the main container and prepares the filesystem.

**Pattern A: Symlink Farm (zero-copy)**
```yaml
initContainers:
- name: prepare-layout
  image: busybox
  command: ['sh', '-c']
  args:
  - |
    # Read manifest from ConfigMap, API, or environment
    while IFS='|' read -r src dst; do
      mkdir -p "$(dirname "/workspace/$dst")"
      ln -s "/archive/$src" "/workspace/$dst"
    done < /config/manifest.txt
  volumeMounts:
  - name: archive
    mountPath: /archive
    readOnly: true
  - name: workspace
    mountPath: /workspace
  - name: manifest
    mountPath: /config
containers:
- name: compute
  volumeMounts:
  - name: archive
    mountPath: /archive    # Must also be mounted for symlinks to resolve
    readOnly: true
  - name: workspace
    mountPath: /input
volumes:
- name: archive
  persistentVolumeClaim:
    claimName: xnat-archive
- name: workspace
  emptyDir: {}
- name: manifest
  configMap:
    name: job-manifest
```

**Pattern B: Selective Copy (from object storage)**
```yaml
initContainers:
- name: stage-data
  image: minio/mc
  command: ['sh', '-c']
  args:
  - |
    mc alias set minio http://minio:9000 $ACCESS_KEY $SECRET_KEY
    mc cp minio/archive/path/to/file1.dcm /workspace/input/file1.dcm
    mc cp minio/archive/path/to/file2.dcm /workspace/input/file2.dcm
  volumeMounts:
  - name: workspace
    mountPath: /workspace
```

**Pattern C: Reflink Copy (zero-space CoW, same filesystem)**
```yaml
initContainers:
- name: prepare-writable
  image: busybox
  command: ['sh', '-c']
  args:
  - |
    # Create reflink copies — instant, zero additional space
    while IFS='|' read -r src dst; do
      mkdir -p "$(dirname "/workspace/$dst")"
      cp --reflink=always "/archive/$src" "/workspace/$dst"
    done < /config/manifest.txt
  volumeMounts:
  - name: shared-storage   # Must be same filesystem for reflinks
    mountPath: /archive
    subPath: archive
  - name: shared-storage
    mountPath: /workspace
    subPath: workspaces/job-123
```

**Verdict**: Init containers are the proven, pragmatic approach. Pattern A (symlinks) for read-only access, Pattern C (reflinks) for writable access (if same filesystem), Pattern B (copy) as fallback.

### 2.4 FUSE Sidecar + Mutating Webhook

The pattern proven by Google's GCSFuse CSI driver:

1. A **mutating admission webhook** intercepts pod creation
2. If the pod has specific annotations (e.g., `scout.io/file-view: my-view-spec`), the webhook injects:
   - A **sidecar container** running the FUSE daemon
   - Volume mounts with `mountPropagation: Bidirectional`
   - The necessary security context for FUSE
3. The sidecar mounts the virtual filesystem; the main container sees it as a regular directory

**Strengths**:
- Transparent to the application — no code changes needed
- Dynamic — the FUSE daemon can serve different views per pod
- Established pattern (GCSFuse, Vault Agent Injector, Istio)

**Limitations**:
- Requires `privileged: true` or at minimum `/dev/fuse` access and `SYS_ADMIN` capability
- FUSE sidecar must stay running for the mount to work (if it crashes, the mount goes stale)
- Adds resource overhead per pod

### 2.5 Custom CSI Driver

A CSI (Container Storage Interface) driver can present arbitrary virtual filesystems as Kubernetes PersistentVolumes.

**How it would work**:
1. Define a `StorageClass` for the file view driver
2. Create PVCs with parameters specifying the view (source, layout spec, writable flag)
3. The CSI driver's `NodeStageVolume`/`NodePublishVolume` implementations set up the view (FUSE mount, symlink farm, overlay, etc.)
4. The pod mounts the PVC normally — no special annotations or sidecars

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: scout-file-view
provisioner: scout.example.com/file-view
parameters:
  source: xnat-archive
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ct-scans-2024
  annotations:
    scout.example.com/view-spec: |
      source: xnat-archive
      filter:
        modality: CT
        year: 2024
      layout: flat
      writable: false
spec:
  storageClassName: scout-file-view
  accessModes: [ReadOnlyMany]
  resources:
    requests:
      storage: 1Ti   # virtual; actual storage is the archive
```

**Strengths**:
- Most Kubernetes-native approach — pods just mount a PVC
- No application changes, no special annotations
- CSI handles lifecycle (mount, unmount, cleanup)
- Works with any pod (no webhook needed)

**Limitations**:
- Building a CSI driver is significant engineering effort
- Must handle FUSE lifecycle, error recovery, metrics
- CSI spec has constraints (volume staging, publishing phases) that add complexity

### 2.6 Custom Operator with CRD

Define a `FileView` CRD and build an operator that manages the lifecycle:

```yaml
apiVersion: scout.example.com/v1alpha1
kind: FileView
metadata:
  name: ct-scans-2024
spec:
  source:
    type: pvc
    name: xnat-archive
  selector:
    modality: CT
    dateRange:
      start: "2024-01-01"
      end: "2024-12-31"
  layout:
    structure: flat          # or: by-subject, by-date, custom
    # custom layout DSL (see Part 4)
  access:
    mode: ReadOnly           # or: CopyOnWrite
    concurrency: unlimited
status:
  phase: Ready
  mountPath: /views/ct-scans-2024
  fileCount: 15432
  generatedPVC: ct-scans-2024-view
```

The operator would:
1. Watch for `FileView` resources
2. Resolve the selector against a metadata catalog (XNAT's database, a custom index, etc.)
3. Provision the view (create symlink farm, FUSE mount, reflink tree, or PVC clone)
4. Create a PVC or ConfigMap that pods can reference
5. Clean up when the `FileView` is deleted

**Strengths**:
- Declarative, Kubernetes-native
- Separation of concerns — users declare what they want, operator figures out how
- Can choose the best mechanism (symlinks, reflinks, FUSE) based on the situation
- Natural place to inject a metadata broker that translates view specs into file paths

**Limitations**:
- Requires building an operator (though operator frameworks like kubebuilder simplify this)
- The metadata resolution step (selector → file paths) requires integration with XNAT or a custom catalog

---

## Part 3: Data Orchestration and Caching Layers

### 3.1 Fluid (CNCF Sandbox)

**URL**: https://github.com/fluid-cloudnative/fluid

Dataset orchestration for Kubernetes. Provides `Dataset` and `DataLoad` CRDs with pluggable runtimes.

**Key features**:
- `Dataset` CRD defines data sources (S3, HDFS, NFS)
- Runtimes (Alluxio, JuiceFS, ThinRuntime) cache data on local nodes
- Data-aware scheduling (co-locates pods with cached data)
- `DataLoad` CRD for pre-loading data before jobs start
- **ThinRuntime**: Lightweight wrapper for any FUSE filesystem (s3fs, rclone, etc.)

**Relevance**: High for caching and data locality. Does NOT support custom file layouts — you get the source's directory structure. Could be extended or combined with a view layer.

**Access mode requirements**: Depends on the runtime. Alluxio runtime needs ReadWriteMany for its worker cache volumes.

### 3.2 Alluxio

**URL**: https://github.com/Alluxio/alluxio

Distributed data orchestration with a unified namespace.

**Key feature for this use case**: **Nested mounts** — you can mount different storage paths at different virtual paths within Alluxio's namespace:
```
/alluxio/ct-scans/  →  s3://archive/project1/.../CT/
/alluxio/ct-scans/  ←  also includes s3://archive/project2/.../CT/
/alluxio/mri-scans/ →  s3://archive/project1/.../MR/
```

This is the closest existing tool to "virtual file layout." The unified namespace IS a custom layout over heterogeneous storage.

**Limitations**:
- Heavy operational footprint (master, workers, FUSE daemons)
- Per-pod custom layouts would require per-pod Alluxio configurations
- Community Edition has feature limitations

### 3.3 JuiceFS

**URL**: https://github.com/juicedata/juicefs

POSIX-compliant distributed filesystem over object storage + metadata engine.

**Relevance**: Since JuiceFS is a full POSIX filesystem, you can create symlinks within it. An init container or operator could build symlink-based views on a JuiceFS volume backed by MinIO. The CSI driver supports ReadWriteMany.

**Architecture**: Metadata in Redis/PostgreSQL/TiKV, data in object storage (MinIO). This separation means the same data blocks can be referenced from multiple paths (via symlinks or hardlinks) without duplication.

### 3.4 CVMFS (CernVM File System)

**URL**: https://cernvm.cern.ch/fs/

Read-only, content-addressed, lazy-loading filesystem. Used at CERN and across HPC.

**Key insight**: CVMFS uses a **catalog** (metadata file) that defines a virtual directory tree. Each entry points to a content-addressed blob. Changing the catalog changes the view without moving data. This is architecturally very close to what we want — a metadata-driven projection over content-addressed storage.

Has a Kubernetes CSI driver.

**Limitations**: Read-only. Designed for software distribution, not arbitrary data. Publishing requires a specific server infrastructure.

### 3.5 Datashim (formerly Dataset Lifecycle Framework)

**URL**: https://github.com/datashim-io/datashim

A `Dataset` CRD that abstracts data sources. Uses a **mutating webhook** to inject volume mounts into pods that reference datasets via labels (`dataset.0.id: my-dataset`).

**Limitations**: Still presents native storage layout. Less actively maintained.

---

## Part 4: Toward a Design — The View Specification

No existing tool combines declarative layout specification with zero-copy file presentation. This section sketches what a purpose-built system might look like.

### 4.1 Inspirations for a Layout DSL

| System | How it declares layout | Mechanism |
|--------|----------------------|-----------|
| **Nix** | Nix expression language (`symlinkJoin`, `buildEnv`) | Symlink farms from content-addressed store |
| **Kubernetes Projected Volumes** | YAML `sources` with `items[].path` | kubelet assembles tmpfs |
| **Dockerfile** | `COPY --from` with path mappings | Layer-based overlay |
| **Nextflow** | Process `input: path(x)` declarations | Symlink staging |
| **CWL** | `File` type with `location` and `path` | Runner-dependent staging |
| **Bazel** | BUILD rules declaring inputs/outputs | Sandboxed mount namespaces |
| **ProjFS** (Windows) | Programmatic callbacks | Kernel-level on-demand projection |
| **Nydus** | Separate metadata tree from content chunks | FUSE with on-demand loading |
| **GNU Stow** | Directory structure convention | Symlink farms |

### 4.2 Sketch: View Specification Schema

```yaml
apiVersion: scout.example.com/v1alpha1
kind: FileView
metadata:
  name: training-set-ct-2024
spec:
  # Where to find file metadata (for resolving selectors to paths)
  catalog:
    type: xnat               # or: custom-api, delta-table, static
    endpoint: http://xnat.internal/data/archive
    # For delta-table type:
    # table: delta.default.reports
    # connection: trino://trino:8080

  # Which files to include
  selector:
    # Filter expressions — these query the catalog
    filters:
    - field: modality
      operator: eq
      value: CT
    - field: message_dt
      operator: between
      value: ["2024-01-01", "2024-12-31"]
    - field: sending_facility
      operator: in
      value: ["HOSP_A", "HOSP_B"]
    # Could also support raw queries:
    # query: "SELECT file_path FROM reports WHERE modality = 'CT' AND year = 2024"

  # How to arrange the selected files
  layout:
    # Predefined layouts
    # structure: flat                      # All files in one directory
    # structure: by-field                  # Group by a metadata field
    #   field: sending_facility

    # Or a template-based custom layout
    structure: template
    pathTemplate: "{{ sending_facility }}/{{ subject_id }}/{{ series_uid }}/{{ filename }}"

    # Or for maximum flexibility, a Starlark/CEL expression
    # structure: expression
    # expression: |
    #   file.modality + "/" + file.date.format("YYYY/MM") + "/" + file.name

  # Access mode
  access:
    mode: ReadOnly              # Default: read-only, zero-copy
    # mode: CopyOnWrite         # Writable; uses reflinks if available, falls back to full copy
    # mode: FullCopy            # Always copy (when source is remote or different filesystem)

  # Implementation hints (operator chooses best available)
  hints:
    preferredMechanism: symlink  # symlink | reflink | fuse | copy
    cacheLocally: false          # Whether to cache on the node (for remote sources)

status:
  phase: Ready
  mechanism: symlink            # What the operator actually used
  fileCount: 15432
  totalSize: 48.2Gi             # Logical size (not physical, since zero-copy)
  physicalOverhead: 1.2Mi       # Space used by symlinks/metadata
  pvcName: training-set-ct-2024-view
  conditions:
  - type: Ready
    status: "True"
  - type: CatalogSynced
    status: "True"
    lastSyncTime: "2026-04-10T12:00:00Z"
```

### 4.3 Mechanism Selection Logic

The operator would choose the best mechanism based on the situation:

```
Is the source a local PV mounted on the same node?
├─ Yes
│   ├─ Access mode: ReadOnly?
│   │   ├─ Yes → Symlink farm (fastest, simplest)
│   │   └─ No (CopyOnWrite)
│   │       ├─ Filesystem supports reflinks? → Reflink copies
│   │       └─ No → Full copy to emptyDir
│   └─ Custom layout needed?
│       ├─ Yes → Symlink/reflink farm with remapped paths
│       └─ No → Direct mount (subPath or whole PV)
├─ No (source is S3/MinIO/remote)
│   ├─ Cache locally?
│   │   ├─ Yes → Download to local emptyDir, arrange per layout
│   │   └─ No → FUSE mount (s3fs, GeeseFS, JuiceFS)
│   └─ Custom layout needed over FUSE?
│       ├─ Yes → FUSE sidecar + symlink farm on emptyDir
│       └─ No → Direct FUSE mount of the prefix
└─ Fallback → Init container with selective copy
```

---

## Part 5: Relevant Architectural Patterns

### 5.1 The Broker/Catalog Pattern

Inspired by Crossplane's "Claim → Composite → Managed Resource" model and the Open Service Broker API:

1. **User claims a view** — `FileView` CRD with selector and layout spec
2. **Broker resolves the claim** — queries XNAT/catalog for matching files, resolves to physical paths
3. **Broker provisions the view** — creates the symlink farm, FUSE mount, or PVC clone
4. **Pod consumes the view** — mounts the generated PVC or uses injected volume

The broker (operator) is the key component. It must:
- Speak to the catalog (XNAT REST API, database, Delta Lake table)
- Translate metadata queries into file paths
- Choose and execute the provisioning mechanism
- Manage lifecycle (cleanup views when no longer needed)

### 5.2 XNAT Container Service Precedent

XNAT's own Container Service already solves a version of this problem:

1. User selects a session/scan in XNAT UI
2. XNAT resolves the selection to archive filesystem paths
3. XNAT creates Docker bind mounts mapping archive paths into the container
4. Container runs with read-only access to input files and a writable output directory
5. On completion, XNAT uploads outputs back to the archive

The difference from our use case: XNAT's Container Service uses XNAT's native hierarchy and Docker bind mounts (not Kubernetes). We want Kubernetes-native orchestration with arbitrary layouts.

### 5.3 Content-Addressed Storage + Metadata Tree (Nydus Pattern)

The Nydus container image format separates:
- **Metadata tree**: A compact description of the directory structure (paths, permissions, sizes)
- **Data chunks**: Content-addressed blobs (deduplicated, compressed)

A FUSE daemon serves the filesystem by reading the metadata tree for directory operations and fetching data chunks on demand for file reads.

Applying this to our use case:
- **Metadata tree** = The view specification (which files, at what paths)
- **Data chunks** = The actual DICOM/archive files in MinIO or on the PV
- **FUSE daemon** = Custom filesystem that maps view paths to archive files

This is arguably the cleanest architecture for a fully custom solution.

### 5.4 Plan 9 Per-Process Namespaces

Plan 9's model — every process gets its own filesystem namespace, composed by selectively binding directories — is the theoretical ideal. Linux mount namespaces approximate this, and Kubernetes pods are already isolated mount namespaces.

**bubblewrap** (`bwrap`) is a command-line tool that constructs custom per-process filesystem views using mount namespaces and bind mounts:
```bash
bwrap --ro-bind /archive/path1 /input/scan1 \
      --ro-bind /archive/path2 /input/scan2 \
      --bind /tmp/output /output \
      -- /usr/bin/my-compute-app
```

This could be used inside an init container or entrypoint wrapper, though it requires `CAP_SYS_ADMIN`.

### 5.5 LD_PRELOAD / POSIX Interception

Intercept libc filesystem calls to redirect file access without any kernel involvement:

```c
// Pseudo-code for an LD_PRELOAD library
int open(const char *pathname, int flags) {
    const char *remapped = lookup_remap(pathname);  // Check remap table
    return real_open(remapped ? remapped : pathname, flags);
}
```

**Strengths**: No kernel modules, no FUSE, no privileges, near-zero overhead.
**Limitations**: Does not work with statically linked binaries or Go programs (which use raw syscalls, not libc). Not robust for production use.

**Parrot** (from CCTools/Notre Dame) is a mature LD_PRELOAD tool that redirects I/O to remote storage for scientific computing.

---

## Part 6: Comparison Matrix

### Mechanism Comparison

| Mechanism | Custom Layout | Zero-Copy | CoW Support | No Privileges | K8s Native | Production Maturity |
|-----------|:---:|:---:|:---:|:---:|:---:|:---:|
| Direct PV mount | - | Yes | - | Yes | Yes | Proven |
| SubPath mounts | Limited | Yes | - | Yes | Yes | Proven |
| Init container + symlinks | **Yes** | **Yes** | - | **Yes** | **Yes** | **Proven** |
| Init container + reflinks | **Yes** | Near | **Yes** | **Yes** | **Yes** | Proven (if FS supports) |
| Init container + copy | **Yes** | - | **Yes** | **Yes** | **Yes** | Proven |
| OverlayFS | - | Yes | **Yes** | - | - | Proven (but privileged) |
| Custom FUSE filesystem | **Yes** | **Yes** | **Yes** | Partial* | Via CSI | Build required |
| FUSE sidecar + webhook | **Yes** | **Yes** | **Yes** | Partial* | **Yes** | Proven pattern |
| Custom CSI driver | **Yes** | **Yes** | **Yes** | **Yes** | **Yes** | Build required |
| Custom CRD + operator | **Yes** | **Yes** | **Yes** | **Yes** | **Yes** | Build required |
| Alluxio nested mounts | **Yes** | Cached | Configurable | **Yes** | Via CSI | Proven (heavy) |
| JuiceFS + symlinks | **Yes** | Cached | **Yes** | **Yes** | Via CSI | Proven |
| Fluid + ThinRuntime | - | Cached | Configurable | **Yes** | **Yes** | Proven |
| LD_PRELOAD interception | **Yes** | **Yes** | Possible | **Yes** | - | Fragile |

\* FUSE requires `/dev/fuse` access; does not require full `CAP_SYS_ADMIN` in newer setups.

### Approach Comparison by Use Case

| If you need... | Best approach |
|----------------|--------------|
| Simplest possible solution | Init container + symlinks |
| Zero-copy + custom layout + read-only | Init container + symlinks |
| Writable copies + custom layout | Init container + reflinks (same FS) or copies |
| Transparent to applications (no symlink awareness) | Custom FUSE filesystem |
| Kubernetes-native declarative API | Custom CRD + operator |
| Caching for repeated access patterns | Fluid or Alluxio |
| Minimal infrastructure additions | Init container patterns |
| Maximum flexibility for future evolution | Custom CRD + operator + pluggable mechanisms |

---

## Part 7: Recommended Architecture

### Tiered Approach

Rather than committing to one mechanism, build a system that can use different mechanisms depending on the situation:

**Tier 1 — Immediate (init container + symlinks)**
- Build a "view provisioner" script/container that reads a manifest (JSON/YAML) and creates a symlink farm in an emptyDir
- The manifest is generated by a "broker" service that queries XNAT/catalog for matching files and resolves paths
- Pods mount the archive PV (read-only) and the emptyDir (symlink farm)
- For writable access, use reflinks (if same FS) or copies as fallback
- This works today with no new infrastructure

**Tier 2 — Near-term (CRD + operator)**
- Define a `FileView` CRD (see Section 4.2)
- Build an operator that watches `FileView` resources and provisions views
- The operator generates the init container configuration (or directly creates the symlink farm on a shared volume)
- Adds declarative API, lifecycle management, status reporting

**Tier 3 — Future (custom FUSE CSI driver)**
- Build a FUSE filesystem that serves virtual views based on a mapping specification
- Package as a CSI driver for seamless Kubernetes integration
- Transparent to applications — no symlink awareness needed, no archive PV mount in consumer pods
- Supports on-demand loading (lazy — only fetch files when accessed)
- Supports CoW natively within the FUSE layer

### Component Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     User / Job Submitter                     │
│                                                              │
│  "I need CT scans from 2024, grouped by facility,           │
│   read-only, for my ML training job"                         │
└──────────────────────────┬──────────────────────────────────┘
                           │ Creates FileView CR
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    FileView Operator                         │
│                                                              │
│  1. Reads FileView spec (selector, layout, access mode)     │
│  2. Queries catalog for matching files                       │
│  3. Resolves file paths in the archive                       │
│  4. Chooses provisioning mechanism                           │
│  5. Creates view (symlink farm / FUSE mount / reflink tree) │
│  6. Exposes view as PVC or injects via webhook               │
└──────────┬───────────────┬──────────────────────────────────┘
           │               │
           ▼               ▼
┌──────────────┐  ┌────────────────┐
│   Catalog    │  │  View Store    │
│              │  │                │
│  XNAT API   │  │  Symlink farm  │
│  Delta Lake  │  │  FUSE mount    │
│  Custom DB   │  │  Reflink tree  │
│              │  │  PVC clone     │
└──────────────┘  └───────┬────────┘
                          │ Mounted as PVC
                          ▼
                 ┌─────────────────┐
                 │   Compute Pod   │
                 │                 │
                 │  /input/        │
                 │    facility_a/  │
                 │      scan1.dcm  │  ← symlink → /archive/.../scan1.dcm
                 │      scan2.dcm  │  ← symlink → /archive/.../scan2.dcm
                 │    facility_b/  │
                 │      scan3.dcm  │  ← symlink → /archive/.../scan3.dcm
                 └─────────────────┘
```

### Open Questions

1. **Catalog integration**: How does the operator query XNAT for file metadata? XNAT REST API? Direct database access? A pre-built index/catalog?

2. **Freshness**: How often does the view need to reflect changes in the archive? Static (snapshot at creation time) or live (auto-updating)?

3. **Scale**: How many files per view? Thousands of symlinks are fast; millions may need a FUSE approach for directory listing performance.

4. **Namespace isolation**: Should views be namespace-scoped (tied to a team/project) or cluster-wide?

5. **Garbage collection**: When and how are views cleaned up? TTL? Reference counting (delete when no pods use it)?

6. **Security**: Who can create views? Should the operator enforce access control based on XNAT project permissions?

---

## Appendix A: Tool Reference

| Tool | URL | Category |
|------|-----|----------|
| OverlayFS | (Linux kernel built-in) | Kernel filesystem |
| bindfs | https://bindfs.org/ | FUSE mirror mount |
| mergerfs | https://github.com/trapexit/mergerfs | FUSE union mount |
| unionfs-fuse | https://github.com/rpodgorny/unionfs-fuse | FUSE union mount |
| rofs-filtered | https://github.com/gburber/rofs-filtered | FUSE filtered view |
| s3fs-fuse | https://github.com/s3fs-fuse/s3fs-fuse | S3 FUSE mount |
| GeeseFS | https://github.com/yandex-cloud/geesefs | S3 FUSE mount (perf) |
| JuiceFS | https://github.com/juicedata/juicefs | Distributed POSIX FS |
| Alluxio | https://github.com/Alluxio/alluxio | Data orchestration |
| Fluid | https://github.com/fluid-cloudnative/fluid | K8s dataset orchestration |
| Datashim | https://github.com/datashim-io/datashim | K8s dataset CRD |
| CVMFS | https://cernvm.cern.ch/fs/ | Content-addressed FS |
| Rook/Ceph | https://github.com/rook/rook | Distributed storage |
| Mountpoint for S3 | https://github.com/awslabs/mountpoint-s3 | S3 FUSE (AWS only) |
| GCSFuse CSI | https://github.com/GoogleCloudPlatform/gcs-fuse-csi-driver | GCS FUSE CSI |
| csi-s3 | https://github.com/ctrox/csi-s3 | S3 CSI driver |
| GNU Stow | https://www.gnu.org/software/stow/ | Symlink farm manager |
| bubblewrap | https://github.com/containers/bubblewrap | Namespace sandbox |
| NFS Ganesha | https://github.com/nfs-ganesha/nfs-ganesha | Userspace NFS server |
| Nydus | https://github.com/dragonflyoss/nydus | Lazy-load container images |
| eStargz | https://github.com/containerd/stargz-snapshotter | Lazy-load container images |
| SOCI | https://github.com/awslabs/soci-snapshotter | Lazy-load (AWS) |
| DataLad | https://www.datalad.org/ | Data management (Git-based) |
| libfuse | https://github.com/libfuse/libfuse | FUSE library (C) |
| go-fuse | https://github.com/hanwen/go-fuse | FUSE library (Go) |
| fuser | https://github.com/cberner/fuser | FUSE library (Rust) |
| Parrot (CCTools) | https://cctools.readthedocs.io/en/latest/parrot/ | I/O interception |
| Nextflow | https://www.nextflow.io/ | Scientific workflow engine |
| Arvados | https://arvados.org/ | CWL runner with CAS + FUSE |
| LakeFS | https://lakefs.io/ | Data versioning |

## Appendix B: Precedent Systems

| System | What it does that's relevant |
|--------|------------------------------|
| **XNAT Container Service** | Resolves metadata → file paths, creates bind mounts for containers |
| **Nextflow** | Symlink-based zero-copy staging with `stageInMode` control |
| **Nix/Guix** | Declarative DSL for constructing filesystem views from CAS via symlink profiles |
| **ProjFS (Windows)** | Fully programmable filesystem projection with on-demand materialization |
| **Nydus** | Separate metadata tree from content-addressed data, FUSE-served |
| **CVMFS** | Catalog-driven virtual directory tree over content-addressed blobs |
| **Argo Workflows** | Init container artifact staging from S3 |
| **Docker/containerd** | OverlayFS layered composition of filesystem views |
| **Crossplane** | "Claim → Composite → Managed Resource" pattern for declarative resource provisioning |
| **Vault Agent Injector** | Mutating webhook → sidecar injection pattern |
