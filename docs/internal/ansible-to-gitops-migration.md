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

## Commands

Placeholders: `<ns>` namespace, `<cluster>`/`<name>`/`<tenant>`/`<dc>` the CR name,
`<pv>` the bound PersistentVolume, `<old-pvc>`/`<new-pvc>` the PVC names. Run these
per stateful service, in the step order above.

### 1. Inventory the live PVC names

```sh
kubectl -n <ns> get pvc | grep <cluster>                                    # Cassandra
kubectl -n <ns> get pvc -l cnpg.io/cluster=<cluster>                        # Postgres (CNPG)
kubectl -n <ns> get pvc -l elasticsearch.k8s.elastic.co/cluster-name=<name> # Elasticsearch (ECK)
kubectl -n <ns> get pvc -l v1.min.io/tenant=<tenant>                        # MinIO (on-prem)
```

Compare against the artifact-rendered name. The names are deterministic from the
site `cluster-vars`, so the simplest check is to plug those values into the
patterns above (e.g. rack `r1` gives `server-data-<cluster>-<dc>-r1-sts-0`). To
render straight from the base instead, export the vars and substitute:

```sh
eval "$(kubectl -n <flux-ns> get cm cluster-vars \
  -o go-template='{{range $k,$v := .data}}export {{$k}}={{printf "%q" $v}}{{"\n"}}{{end}}')"
kustomize build deploy/base/cassandra/datacenter | envsubst
```

### 2. Snapshot every live PVC (the net)

```sh
kubectl -n <ns> apply -f - <<EOF
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: <old-pvc>-premigration
spec:
  volumeSnapshotClassName: <snapshot-class>
  source:
    persistentVolumeClaimName: <old-pvc>
EOF
```

Cloud-native alternative (resolve the volume handle, snapshot at the provider):

```sh
PV=$(kubectl -n <ns> get pvc <old-pvc> -o jsonpath='{.spec.volumeName}')
kubectl get pv "$PV" -o jsonpath='{.spec.csi.volumeHandle}'   # feed to e.g. aws ec2 create-snapshot
```

### 3. Prune-guard so switchover adopts instead of deleting

```sh
A=kustomize.toolkit.fluxcd.io/prune=disabled
kubectl -n <ns> annotate cassandradatacenter/<dc> "$A"
kubectl -n <ns> annotate cluster.postgresql.cnpg.io/<cluster> "$A"
kubectl -n <ns> annotate elasticsearch/<name> "$A"
kubectl -n <ns> annotate helmrelease/<tenant> "$A"          # MinIO tenant
kubectl annotate ns <ns> "$A"
# ECK: never reclaim the data volume on a reconcile
kubectl -n <ns> patch elasticsearch <name> --type=merge \
  -p '{"spec":{"volumeClaimDeletePolicy":"DeleteOnScaledownOnly"}}'
```

### 4. Rebind a PV (only when the name does not match)

```sh
# quiesce with the operator-blessed stop (keeps PVCs):
kubectl -n <ns> patch cassandradatacenter <dc> --type=merge -p '{"spec":{"stopped":true}}'  # Cassandra
kubectl cnpg hibernate on <cluster> -n <ns>                                                 # Postgres

PV=$(kubectl -n <ns> get pvc <old-pvc> -o jsonpath='{.spec.volumeName}')
kubectl patch pv "$PV" -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
kubectl -n <ns> delete pvc <old-pvc>
kubectl patch pv "$PV" --type=json -p '[{"op":"remove","path":"/spec/claimRef"}]'
kubectl -n <ns> apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: <new-pvc>
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: <same-as-pv>
  resources:
    requests:
      storage: <same-as-pv>
  volumeName: $PV
EOF
kubectl -n <ns> get pvc <new-pvc> -o wide   # expect Bound to $PV
# then un-quiesce (unset stopped / hibernate off) and let Flux reconcile the artifact CR
```

### 5. Verify data, not liveness

```sh
kubectl -n <ns> exec <cassandra-pod> -c cassandra -- nodetool status
kubectl -n <ns> exec <pg-primary> -- psql -U postgres -c '\dt+'
kubectl -n <ns> exec <es-pod> -- curl -s localhost:9200/_cat/indices?v
```

### 6. Rollback: recreate the PVC from the snapshot

```sh
kubectl -n <ns> apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: <old-pvc>
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: <class>
  resources:
    requests:
      storage: <size>
  dataSource:
    name: <old-pvc>-premigration
    kind: VolumeSnapshot
    apiGroup: snapshot.storage.k8s.io
EOF
```

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
