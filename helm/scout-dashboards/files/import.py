"""Force-overwrite Superset Database / dataset / chart / dashboard params by UUID.

`superset import-dashboards` silently preserves existing rows on UUID
match, so edits to the YAML never reach the DB without this script.

Invoked twice by the import Job:

  1. `python import.py <dir> --databases-only` runs BEFORE the CLI to
     heal a stale `Database.sqlalchemy_uri`. Required because the CLI's
     `import_dataset` opens a Trino connection when creating a new
     dataset, and a stale URI aborts the Job before the post-CLI pass
     can heal it.
  2. `python import.py <dir>` (no flag) runs AFTER the CLI. Sweeps
     databases / datasets / charts / dashboards and force-overwrites
     params, position_json, json_metadata, etc. on existing rows.

For dashboards we also remap chartId references in position_json and
json_metadata. YAML files capture chartId values from whatever DB the
export came from, but on import Superset auto-assigns new local IDs.
UUID is the only stable identifier across instances.

Usage: python import.py [analytics_dir] [--databases-only]
"""

import glob
import json
import sys

import yaml

from superset import db  # noqa: E402
from superset.app import create_app  # noqa: E402

app = create_app()
app.app_context().push()

from superset.commands.database.importers.v1.utils import (  # noqa: E402
    import_database,
)
from superset.connectors.sqla.models import (  # noqa: E402
    SqlaTable,
    SqlMetric,
    TableColumn,
)
from superset.models.core import Database  # noqa: E402
from superset.models.dashboard import Dashboard  # noqa: E402
from superset.models.slice import Slice  # noqa: E402

# `--databases-only` runs Pass 0 and exits. Used pre-CLI to heal a stale
# `sqlalchemy_uri` before `superset import-dashboards` opens a Trino
# connection (e.g., when creating a new dataset).
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
ANALYTICS = _args[0] if _args else "/app/dashboard-config/analytics"
DATABASES_ONLY = "--databases-only" in sys.argv[1:]


def _coerce_bool(val) -> bool:
    return bool(val) if val is not None else False


_COLUMN_FIELDS = (
    "verbose_name",
    "type",
    "expression",
    "description",
    "python_date_format",
)
_METRIC_FIELDS = (
    "verbose_name",
    "metric_type",
    "expression",
    "description",
    "d3format",
    "currency",
    "warning_text",
)


def update_database(path: str) -> bool:
    """Sync a Database row's connection fields from the YAML by UUID.

    Delegates to Superset's `import_database` with `overwrite=True`,
    which uses `set_sqlalchemy_uri` (password masking) and
    `Database.import_from_dict` (extra-as-JSON, ssh_tunnel,
    PREVENT_UNSAFE_DB_CONNECTIONS check). `ignore_permissions=True` is
    needed because the import Job has no Flask request context.

    Returns False when no row exists yet. On a clean install the
    upstream CLI creates the row from the YAML.
    """
    with open(path) as f:
        data = yaml.safe_load(f)
    existing = db.session.query(Database).filter_by(uuid=data["uuid"]).first()
    if existing is None:
        return False
    # The helper does `config.pop("allow_csv_upload")` unconditionally
    # (back-compat for pre-PR-16756 export YAMLs). Our chart uses the
    # modern key, so translate here. Delete once Superset drops the
    # legacy rename.
    if "allow_file_upload" in data and "allow_csv_upload" not in data:
        data["allow_csv_upload"] = data.pop("allow_file_upload")
    import_database(data, overwrite=True, ignore_permissions=True)
    return True


def update_dataset(path: str) -> bool:
    """Sync the YAML's columns and metrics into an existing SqlaTable by UUID.

    Superset's import-dashboards skips datasets whose UUID already exists, so
    new calculated columns or metrics in the YAML never reach the DB without
    this. Datasets that don't exist yet get left to import-dashboards (which
    happily creates them).
    """
    with open(path) as f:
        data = yaml.safe_load(f)
    ds = db.session.query(SqlaTable).filter_by(uuid=data["uuid"]).first()
    if ds is None:
        return False

    ds.table_name = data.get("table_name", ds.table_name)
    ds.main_dttm_col = data.get("main_dttm_col", ds.main_dttm_col)
    ds.description = data.get("description", ds.description)
    if "sql" in data:
        ds.sql = data["sql"]
    # Sync cache_timeout from the YAML (datasets ship it as null to inherit the
    # database/global default). Unlike the fields above we always apply it, so a
    # stale per-dataset value is cleared rather than preserved.
    ds.cache_timeout = data.get("cache_timeout")

    existing_cols = {c.column_name: c for c in ds.columns}
    yaml_cols = {c["column_name"]: c for c in data.get("columns") or []}
    for name, spec in yaml_cols.items():
        col = existing_cols.get(name)
        if col is None:
            col = TableColumn(column_name=name)
            ds.columns.append(col)
        for field in _COLUMN_FIELDS:
            if field in spec:
                setattr(col, field, spec[field])
        col.is_dttm = _coerce_bool(spec.get("is_dttm"))
        col.is_active = _coerce_bool(spec.get("is_active", True))
        col.groupby = _coerce_bool(spec.get("groupby", True))
        col.filterable = _coerce_bool(spec.get("filterable", True))

    existing_metrics = {m.metric_name: m for m in ds.metrics}
    yaml_metrics = {m["metric_name"]: m for m in data.get("metrics") or []}
    for name, spec in yaml_metrics.items():
        m = existing_metrics.get(name)
        if m is None:
            m = SqlMetric(metric_name=name)
            ds.metrics.append(m)
        for field in _METRIC_FIELDS:
            if field in spec:
                setattr(m, field, spec[field])
    return True


def update_chart(path: str, chart_id_map: dict[int, int]) -> bool:
    with open(path) as f:
        data = yaml.safe_load(f)
    s = db.session.query(Slice).filter_by(uuid=data["uuid"]).first()
    if s is None:
        return False
    s.slice_name = data["slice_name"]
    s.viz_type = data["viz_type"]
    s.params = json.dumps(data["params"])
    # Rebind to the dataset named by dataset_uuid. Necessary when a chart's
    # YAML moves it to a different dataset — params.datasource follows, but
    # the Slice.datasource_id column is a separate SQLAlchemy field that
    # import-dashboards leaves stale.
    dataset_uuid = data.get("dataset_uuid")
    if dataset_uuid:
        ds = db.session.query(SqlaTable).filter_by(uuid=dataset_uuid).first()
        if ds is not None and (
            s.datasource_id != ds.id or s.datasource_type != "table"
        ):
            s.datasource_id = ds.id
            s.datasource_type = "table"
    qc = data.get("query_context")
    # YAMLs vary: some store query_context as a YAML mapping (parsed to dict),
    # others as a JSON string scalar (parsed to str). Normalize both to a JSON
    # string for the DB column. Don't double-encode the str case.
    if qc is None:
        s.query_context = None
    elif isinstance(qc, str):
        s.query_context = qc
    else:
        s.query_context = json.dumps(qc)
    s.description = data.get("description")

    yaml_slice_id = data.get("params", {}).get("slice_id")
    if isinstance(yaml_slice_id, int):
        chart_id_map[yaml_slice_id] = s.id
    return True


def remap_position(position: dict) -> dict:
    """Set each CHART entry's meta.chartId to the slice's actual DB id (looked
    up by uuid). Anything else passes through untouched."""
    for value in position.values():
        if not isinstance(value, dict) or value.get("type") != "CHART":
            continue
        meta = value.get("meta", {})
        uuid = meta.get("uuid")
        if not uuid:
            continue
        slice_obj = db.session.query(Slice).filter_by(uuid=uuid).first()
        if slice_obj is not None:
            meta["chartId"] = slice_obj.id
    return position


def remap_metadata(metadata: dict, chart_id_map: dict[int, int]) -> dict:
    """Translate chartId references in chart_configuration,
    global_chart_configuration, and native_filter_configuration. Also
    converts native filter targets' `datasetUuid` to `datasetId` — Superset's
    filter widget value-fetch API takes the numeric local id, not the UUID,
    and won't render the dropdown options without it."""

    def remap_list(ids):
        return [chart_id_map.get(i, i) for i in ids]

    cc = metadata.get("chart_configuration") or {}
    new_cc: dict[str, dict] = {}
    for key, val in cc.items():
        try:
            old_id = int(key)
        except (TypeError, ValueError):
            new_cc[key] = val
            continue
        new_id = chart_id_map.get(old_id, old_id)
        if isinstance(val, dict):
            if isinstance(val.get("id"), int):
                val["id"] = chart_id_map.get(val["id"], val["id"])
            cf = val.get("crossFilters") or {}
            if isinstance(cf.get("chartsInScope"), list):
                cf["chartsInScope"] = remap_list(cf["chartsInScope"])
        new_cc[str(new_id)] = val
    if cc:
        metadata["chart_configuration"] = new_cc

    gcc = metadata.get("global_chart_configuration") or {}
    if isinstance(gcc.get("chartsInScope"), list):
        gcc["chartsInScope"] = remap_list(gcc["chartsInScope"])

    for nf in metadata.get("native_filter_configuration") or []:
        if isinstance(nf.get("chartsInScope"), list):
            nf["chartsInScope"] = remap_list(nf["chartsInScope"])
        for target in nf.get("targets") or []:
            if not isinstance(target, dict):
                continue
            ds_uuid = target.pop("datasetUuid", None)
            if ds_uuid and "datasetId" not in target:
                ds = db.session.query(SqlaTable).filter_by(uuid=ds_uuid).first()
                if ds is not None:
                    target["datasetId"] = ds.id

    return metadata


def update_dashboard(path: str, chart_id_map: dict[int, int]) -> bool:
    with open(path) as f:
        data = yaml.safe_load(f)
    d = db.session.query(Dashboard).filter_by(uuid=data["uuid"]).first()
    if d is None:
        return False
    position = remap_position(dict(data["position"]))
    metadata = remap_metadata(dict(data["metadata"]), chart_id_map)
    d.dashboard_title = data["dashboard_title"]
    d.slug = data.get("slug")
    d.position_json = json.dumps(position)
    d.json_metadata = json.dumps(metadata)
    d.description = data.get("description")
    d.published = data.get("published", False)
    return True


# Pass 0: databases. Force-overwrite the SQLAlchemy URI + connection
# flags on existing Database rows so a Helm values change (port, scheme,
# user) propagates.
updated_databases = skipped_databases = 0
for path in sorted(glob.glob(f"{ANALYTICS}/databases/*.yaml")):
    if update_database(path):
        updated_databases += 1
    else:
        skipped_databases += 1
db.session.flush()

if DATABASES_ONLY:
    # Pre-CLI invocation. Commit the URI fix and exit; the post-CLI
    # invocation handles dataset / chart / dashboard passes.
    db.session.commit()
    print(
        f"force-update: databases updated={updated_databases} skipped={skipped_databases}"
    )
    sys.exit(0)

# Pass 1: datasets. Sync columns/metrics on existing datasets by UUID.
updated_datasets = skipped_datasets = 0
for path in sorted(glob.glob(f"{ANALYTICS}/datasets/**/*.yaml", recursive=True)):
    if update_dataset(path):
        updated_datasets += 1
    else:
        skipped_datasets += 1
db.session.flush()

# Pass 2: charts. Build chart_id_map for the dashboard pass.
chart_id_map: dict[int, int] = {}
updated_charts = skipped_charts = 0
for path in sorted(glob.glob(f"{ANALYTICS}/charts/*.yaml")):
    if update_chart(path, chart_id_map):
        updated_charts += 1
    else:
        skipped_charts += 1
db.session.flush()

# Pass 3: dashboards (uses chart_id_map).
updated_dashboards = skipped_dashboards = 0
for path in sorted(glob.glob(f"{ANALYTICS}/dashboards/*.yaml")):
    if update_dashboard(path, chart_id_map):
        updated_dashboards += 1
    else:
        skipped_dashboards += 1

db.session.commit()

print(
    f"force-update: databases updated={updated_databases} skipped={skipped_databases}, "
    f"datasets updated={updated_datasets} skipped={skipped_datasets}, "
    f"charts updated={updated_charts} skipped={skipped_charts}, "
    f"dashboards updated={updated_dashboards} skipped={skipped_dashboards}, "
    f"chart_id_map_size={len(chart_id_map)}"
)
