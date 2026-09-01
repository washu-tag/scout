# Ansible to GitOps migration: stateful volume adoption

The GitOps deployment base (`deploy/`, ADRs 0030/0031) pulls the same workloads
the Ansible install pushes, but Flux reconciles the stateful custom resources
(CassandraDatacenter, CNPG `Cluster`, Elasticsearch, MinIO `Tenant`) from the
artifact. Each operator derives its PersistentVolumeClaim names from fields on the
CR. If an artifact CR renders a PVC name that differs from the one the live
deployment already uses, the operator provisions a **fresh, empty** PVC and
orphans the live one: the workload comes back up with no data, and there is no
error to catch it (the pod is `Healthy` on empty storage).

This document is the preflight that prevents that. It is scoped to **stateful
volume adoption**, the part that risks silent data loss. The end-to-end migration
(secret seeding, Flux DAG ordering, cutover sequencing, rollback) is filled in
when the GitOps path becomes consumable; see `gitops-implementation-plan.md`.

## The rule

Before switching any stateful workload to the artifact, confirm the artifact
renders the **same PVC name(s)** the live workload is using. The operators below
compute those names from CR fields, so a rendered `cluster-vars` value that
disagrees with the live install is enough to strand a volume.

Do not trust the name formulas below as gospel (operator versions change them).
Read the live name directly and compare it to a local render:

```sh
# live
kubectl -n <ns> get pvc

# what the artifact would create (postBuild-substituted with the site cluster-vars)
kustomize build deploy/base/<component>/cluster | kubectl kustomize ... # or a Flux dry-run
```

Match: prune-guard and adopt. Mismatch: reconcile the name (align the artifact or
the `cluster-var`) or rebind the PV (below) **before** switchover.

## Per-service name drivers

| Service | CR | Fields that drive the PVC name | Typical PVC name | Common drift |
|---|---|---|---|---|
| Cassandra | `CassandraDatacenter` (cass-operator) | `clusterName`, `metadata.name` (datacenter), **rack name** | `server-data-<cluster>-<dc>-<rack>-sts-0` | rack name (`r1` vs the default `default` rack when `racks:` is omitted) |
| Postgres | `Cluster` (CNPG) | `metadata.name`, `instances` | `<cluster>-1` (one PVC per instance serial) | cluster name; instance count |
| Elasticsearch | `Elasticsearch` (ECK) | `metadata.name`, nodeSet `name`, volumeClaimTemplate `name` | `elasticsearch-data-<name>-es-<nodeset>-0` | nodeSet name (`default`); cluster name |
| MinIO (on-prem only) | `Tenant` (MinIO operator) | tenant `name`, pool `name`, servers/volumes | `data0-<tenant>-<pool>-0` | tenant name; pool name; server/volume count |

Notes:
- **Cassandra rack is the classic trap.** At `size: 1` the rack is cosmetic (it
  only names the StatefulSet/PVC), so an install can sit on either `r1` or the
  default rack with no functional difference, until adoption, when the name has to
  match. The artifact pins `racks: [{name: r1}]`; an install on the default rack
  must rebind before it adopts.
- **Temporal has no PVC of its own.** Its durable state is Cassandra (history
  store) and Elasticsearch (visibility store), so protecting those two protects
  Temporal. A stranded Cassandra volume is lost workflow history; a stranded ES
  volume is a lost visibility index (rebuildable, but not for free).
- **MinIO is service-mode gated** (ADR 0035): present only in `on-prem` mode. In
  `aws` mode the lake is S3, so there is no MinIO PVC to adopt.

## Adoption mechanics

For each stateful service, in order:

1. **Snapshot first.** Take a volume snapshot (CSI `VolumeSnapshot`, or the
   cloud-native EBS/disk snapshot) of every live stateful PVC. This is the net for
   every step below; do not skip it even when the names match.
2. **Prune-guard the live CR and its namespace.** Annotate the live
   `CassandraDatacenter` / CNPG `Cluster` / `Elasticsearch` / `Tenant` and the
   namespace with `kustomize.toolkit.fluxcd.io/prune: disabled` so the switchover
   **adopts** the running resource instead of cascade-deleting it. For ECK also set
   `volumeClaimDeletePolicy: DeleteOnScaledownOnly` so a reconcile never reclaims
   the data volume.
3. **Verify the PVC name matches** (the rule above). If it matches, the artifact
   CR adopts the existing StatefulSet/PVC in place and you are done for that
   service.
4. **If the name does not match, rebind the PV** (single-node procedure):
   1. Quiesce the workload (for Cassandra, `nodetool drain`; scale the CR to 0 or
      pause its operator).
   2. Set the bound PV's `persistentVolumeReclaimPolicy` to `Retain` so deleting
      the PVC keeps the underlying volume.
   3. Delete the old PVC and StatefulSet; clear the PV's `claimRef` so it returns
      to `Available`.
   4. Pre-create the new-named PVC with `spec.volumeName: <retained PV>` so it
      binds the same volume, then let the artifact CR reconcile onto it.
   5. **AZ note:** cloud block volumes (EBS) are AZ-locked. The bound PV carries a
      `topology.kubernetes.io/zone` node affinity, and the scheduler's volume-
      topology binding places the pod in that AZ automatically (Karpenter/managed
      node autoscalers provision a node there). This only fails if something else
      pins the pod to a different AZ or no node can exist in the volume's AZ, so do
      not add a competing zone selector.
   6. Data-heavy alternative: back up and restore at the application layer
      (Cassandra `nodetool snapshot` then restore; `pg_dump`/restore; ES snapshot
      repository) instead of PV surgery. Topology-agnostic, slower.
5. **Switch over and verify data**, not just liveness: row counts, a known query,
   Temporal workflow visibility. A `Healthy` pod on an empty volume passes a
   liveness probe.
6. **Rollback** is restore-from-snapshot (step 1) onto the original CR.

## Preflight checklist

Per stateful service (Cassandra, Postgres, Elasticsearch, and MinIO in on-prem
mode):

- [ ] Live PVC name(s) recorded (`kubectl get pvc`).
- [ ] Artifact-rendered PVC name(s) compared against live; match confirmed or a
      rebind planned.
- [ ] Volume snapshot taken.
- [ ] Live CR + namespace prune-guarded; ECK `volumeClaimDeletePolicy` set.
- [ ] Post-switchover data verification defined (not liveness).
- [ ] Rollback (restore-from-snapshot) confirmed available.
