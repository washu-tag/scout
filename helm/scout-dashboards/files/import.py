"""Force-overwrite Superset chart and dashboard params by UUID.

Workaround for `superset import-dashboards` not reliably overwriting the
`params`, `query_context`, and `position_json` of existing assets — it
preserves the existing rows and silently ignores the new YAML for any UUID
already in the DB.

Run AFTER `superset import-dashboards`. The CLI handles creating new assets
(datasets, charts, dashboards); this script handles updating existing ones.

For dashboards we also have to remap chartId references in position_json
and json_metadata: YAML files capture chartId values from whatever DB the
export came from, but on import Superset auto-assigns new local IDs by
auto-increment. UUID is the only stable identifier across instances.

Usage: python import.py [analytics_dir]
"""

import glob
import json
import sys

import yaml

from superset import db  # noqa: E402
from superset.app import create_app  # noqa: E402

app = create_app()
app.app_context().push()

from superset.models.dashboard import Dashboard  # noqa: E402
from superset.models.slice import Slice  # noqa: E402

ANALYTICS = sys.argv[1] if len(sys.argv) > 1 else "/app/dashboard-config/analytics"


def update_chart(path: str, chart_id_map: dict[int, int]) -> bool:
    with open(path) as f:
        data = yaml.safe_load(f)
    s = db.session.query(Slice).filter_by(uuid=data["uuid"]).first()
    if s is None:
        return False
    s.slice_name = data["slice_name"]
    s.viz_type = data["viz_type"]
    s.params = json.dumps(data["params"])
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
    global_chart_configuration, and native_filter_configuration."""

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


# Pass 1: charts. Build chart_id_map for the dashboard pass.
chart_id_map: dict[int, int] = {}
updated_charts = skipped_charts = 0
for path in sorted(glob.glob(f"{ANALYTICS}/charts/*.yaml")):
    if update_chart(path, chart_id_map):
        updated_charts += 1
    else:
        skipped_charts += 1
db.session.flush()

# Pass 2: dashboards (uses chart_id_map).
updated_dashboards = skipped_dashboards = 0
for path in sorted(glob.glob(f"{ANALYTICS}/dashboards/*.yaml")):
    if update_dashboard(path, chart_id_map):
        updated_dashboards += 1
    else:
        skipped_dashboards += 1

db.session.commit()

print(
    f"force-update: charts updated={updated_charts} skipped={skipped_charts}, "
    f"dashboards updated={updated_dashboards} skipped={skipped_dashboards}, "
    f"chart_id_map_size={len(chart_id_map)}"
)
