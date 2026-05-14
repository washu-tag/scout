# Scout Dashboards

Helm chart that imports Scout's Superset dashboards, charts, and datasets into
the apache/superset deployment. Owned by the `superset` Ansible role; installed
as a separate Helm release alongside Superset itself.

## How it works

The chart packages a tree of analytics YAML files (the same format
`superset export-dashboards` produces) into a ConfigMap and runs a post-install
Job against the apache/superset chart's image. The Job:

1. Zips the analytics tree from the mounted ConfigMap.
2. Calls `superset import-dashboards` — creates any asset whose UUID is not yet
   in the metadata DB.
3. Runs `import.py`, a force-overwrite Python pass — updates `params`,
   `query_context`, and `position_json` on existing-by-UUID assets, because
   Superset's v1 importer silently skips those. Also remaps `chartId` numeric
   references in `position_json` / `json_metadata` to the actual DB IDs.

The Job is a Helm hook (`post-install,post-upgrade` + `before-hook-creation`),
so it runs on every install and upgrade. Its pod template carries a
`checksum/config` annotation tied to the rendered ConfigMap, which makes the
hash visible in the manifest diff.

## File layout

Built-in dashboards are partitioned by **bundle** — each bundle is a
subdirectory under each asset kind:

```
files/analytics/
├── metadata.yaml
├── charts/
│   ├── core/         # 10 charts on the Scout main dashboard
│   ├── quality/      #  8 charts on the Quality & TAT dashboard
│   └── followup/     # 13 charts on the Follow-up Detection dashboard
├── dashboards/
│   ├── core/
│   ├── quality/
│   └── followup/
└── datasets/
    └── Scout_Data_Lake/
        ├── core/     # reports_latest, reports_dx, reports (raw scale)
        └── followup/ # reports_followup, confusion_matrix_grid
```

`values.yaml` enables bundles by name:

```yaml
bundles:
  enabled: [core]    # add quality, followup to install more
```

The chart's templates iterate the list and `.Files.Glob` only the matching
subdirs into the ConfigMap, so unselected bundles are simply not packaged.
Adding a new built-in bundle is a matter of dropping files into a new
subdirectory and adding its name to `bundles.enabled` — no template edits.

## Adding a new chart, dashboard, or dataset

1. Export the asset from a Superset instance (`superset export-dashboards` or
   the per-dashboard YAML export from the UI).
2. Drop the YAML into the appropriate `files/analytics/<kind>/<bundle>/`
   subdirectory.
3. `helm upgrade` (via `make install-analytics`). The Job re-runs and the new
   asset lands.

Don't generate UUIDs manually — keep the ones Superset assigned. The
force-overwrite pass keys on those.

## Site-specific dashboards

For dashboards that don't belong upstream, two pragmatic options:

- **Add a new bundle here** — drop the YAMLs into a new
  `files/analytics/<kind>/<your-bundle>/` subdir and have your inventory
  set `scout_dashboard_bundles: [core, your-bundle]`. Keeps the dashboards
  versioned alongside the chart.
- **Run a separate site-owned Helm chart** that mounts your dashboard YAMLs
  from its own `files/` and runs its own `superset import-dashboards` Job
  against the same Superset metadata DB. This chart is a working example to
  model from.

## Design rationale

### Why a separate Helm chart instead of staying in the `superset` Ansible role?

The previous design built the ConfigMap by walking `analytics/superset/` from
the Ansible controller and posting it via `kubernetes.core.k8s`. That made
change detection awkward — every deploy had to diff the on-disk YAMLs against
the cluster's ConfigMap, and the Superset init Job had to be re-triggered by
hand if the data changed. Moving to a Helm chart hands change detection to
Helm itself: any analytics file edit re-hashes the ConfigMap, the Job's
checksum/config annotation changes, Helm replaces the Job under
`before-hook-creation`, and the import re-runs. No Ansible-side bookkeeping.

### Why bundles instead of one big import?

The Quality & TAT and Follow-up Detection dashboards include experimental
features and depend on data products (TAT calculations, the follow-up
classifier) that not every Scout site runs. Bundling them lets sites opt in
without modifying the chart. The Scout core dashboard ships by default and is
the only bundle that's always on.

### Why the force-overwrite Python pass on top of `import-dashboards`?

`superset import-dashboards` in 5.x is a v1 importer. For any UUID already
in the metadata DB it leaves the row alone — so editing a chart's `params`
or a dashboard's `position_json` in source has no effect on existing
installations. `import.py` is a thin SQLAlchemy pass that updates the few
fields we care about, keyed by UUID. Critically, it also remaps numeric
`chartId` references in `position_json` and `json_metadata` to the slice IDs
this Superset instance actually assigned (these IDs differ per environment;
UUIDs are the only stable identifier).

### Why is the ConfigMap named `dashboard-config` (not `{{ fullname }}-config`)?

The `superset` Ansible role contains a one-time migration task that deletes a
pre-Helm `dashboard-config` ConfigMap (which lacks Helm ownership annotations)
so this chart can take it over cleanly. The well-known name is the
coordination handle. There's only ever one scout-dashboards install per
cluster, so the lack of fullname-prefixing doesn't cause collisions.

## One-way sync

The Job only imports / updates. It never deletes. Removing a bundle from
`bundles.enabled` or removing a file from a bundle stops new installs from
receiving those assets but doesn't remove already-imported dashboards /
charts / datasets from existing Superset installations. To drop a stale
asset, delete it via the Superset UI or via a `superset fab` invocation (or
surgically via SQLAlchemy in the Superset pod).
