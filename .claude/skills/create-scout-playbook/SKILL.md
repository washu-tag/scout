---
name: create-scout-playbook
description: Create and publish interactive Scout playbook dashboards that appear on the Launchpad. Use this when the user wants to create a new analytics dashboard, visualization, or playbook for Scout.
argument-hint: "[description of the playbook]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion
---

# /create-scout-playbook

Create and publish an interactive Voila dashboard that appears as a card on the Scout Launchpad.

## Workflow

### Step 1: Gather requirements

Default to a single `AskUserQuestion` call with up to 4 questions — the Q&A is part of the value of this skill. Only skip Q&A if the user's prompt already covers data scope, time range, *and* either chart types or use case. When in doubt, ask.

Pick the 3-4 most relevant questions from this set (don't ask all six):

1. **Data scope** — All modalities; specific modality (CT, MRI, XR, US, NM, MG, PET); body region; or a text-match filter
2. **Primary use case** — Operational (volumes, TAT, utilization); clinical review (timelines, case finding); QA (completeness, compliance); research/exploration
3. **Time range default** — 30 days; 90 days; 1 year; 2+ years
4. **Chart types** (multi-select) — Trend lines; summary cards; bar charts; pie/donut; data tables; scatter plots
5. **Grouping** — By modality; by facility; by radiologist; by time period; by demographics
6. **Card appearance** — Icon and color (see lists below)

**Icons:** `users`, `chart`, `sparkles`, `clipboard`, `document`, `beaker`

**Colors:** `violet`, `rose`, `cyan`, `emerald`, `amber`, `blue`, `indigo`, `pink`

Suggested pairings: TAT/quality → `clipboard` + `emerald`; volume/utilization → `chart` + `amber`; radiologists → `users` + `violet`; cohort/case finding → `beaker` + `cyan`; AI/ML → `sparkles` + `pink`; report viewer → `document` + `blue`.

### Step 2: Create the playbook

**Read `sample_dashboard.py` in this skill directory first** — it is the canonical reference for code patterns. Adapt its structure (don't copy-paste blindly; tailor sections, queries, and visuals to the requirements).

Create files in `analytics/notebooks/{playbook-id}/`:

- `{PlaybookName}.ipynb` — single cell that imports the dashboard module and calls `create_landing_page(...)`
- `{module_name}.py` — dashboard module following the sample's patterns

Notebook body:
```python
from {module_name} import create_landing_page

create_landing_page(table_name="default.reports", date_range_days=360)
```

### Step 3: Smoke-test

Before publishing, verify the module parses (don't use `python -m py_compile` — it writes a `.pyc` into `__pycache__/` that breaks the Ansible voila role's ConfigMap step on the next deploy):

```bash
python3 -c "compile(open('analytics/notebooks/{playbook-id}/{module_name}.py').read(), '{module_name}.py', 'exec')" && echo OK
```

Fix any errors before continuing. (Catches syntax errors locally; runtime errors only show up in Voila.)

### Step 4: Publish

```bash
./scripts/scout-publish.sh analytics/notebooks/{playbook-id} \
  --title "Title" \
  --description "Description" \
  --icon "icon" \
  --color "color"
```

The script `kctl cp`s files into the Voila pod's PVC, updates the Launchpad ConfigMap, then rolls the Launchpad deployment so the new card appears immediately (~10s rollout). The user just needs to refresh. The script reads `KUBECONFIG`; if the cluster isn't the default, pass `--kubeconfig PATH` or set the env var.

## Code patterns (full reference: `sample_dashboard.py`)

### Date anchoring (REQUIRED — don't anchor to `now()`)

Anchor the cutoff to `MAX(COALESCE(results_report_status_change_dt, message_dt))` from the table, not `datetime.now()`. The Scout demo dataset is synthetic and tops out at a fixed date in the past; anchoring to `now()` produces an empty dataframe and a "no data" dashboard. For live production data, `max(dt) ≈ now()` so behavior is unchanged.

```python
cursor.execute(
    f"SELECT MAX(COALESCE(results_report_status_change_dt, message_dt)) "
    f"FROM delta.{schema}.{table}"
)
max_dt = cursor.fetchone()[0]
anchor = pd.Timestamp(max_dt)
if anchor.tz is not None:
    anchor = anchor.tz_localize(None)
cutoff_ts = anchor - pd.Timedelta(days=date_range_days)
cutoff = cutoff_ts.strftime("%Y-%m-%d")
```

Then use `cutoff` in the WHERE clause instead of a `datetime.now()`-derived value. See `_load_quality_data` in `sample_dashboard.py` for the full pattern.

### TAT calculation

`requested_dt` (when the order was placed) is the preferred start. `observation_dt` is the fallback. Use the COALESCE in this order to get the patient-experience TAT.

```sql
CAST(DATE_DIFF('second',
    COALESCE(requested_dt, observation_dt),
    COALESCE(results_report_status_change_dt, message_dt)
) AS DOUBLE) / 3600.0 AS order_to_report_hours
```

Filter outliers in Python: set `(hours <= 0) | (hours > 720)` to NULL.

### Radiologist filter

Filter at *render time*, not in WHERE — the WHERE form would drop rows useful for non-radiologist panels in the same dashboard.

```python
rad_named = df["radiologist"].notna() & (df["radiologist"].astype(str).str.strip() != "")
df_rads = df[rad_named]
```

### Partition pruning

The `reports` table is partitioned by `year` (derived from `message_dt`). Add `year IN (cutoff_ts.year, ..., anchor.year)` alongside the cutoff filter so Trino prunes partitions before scanning. The full pattern in `sample_dashboard.py` does both anchoring and pruning together.

### Trino connection

Use `_connect_trino()` from `sample_dashboard.py`. Reads `TRINO_HOST`, `TRINO_PORT`, etc. from env with defaults that work in-cluster.

### Visual conventions

- Header / accent: `#667eea`. Gradient: `#667eea → #764ba2`. Success: `#10b981 → #059669`.
- Tables: `display(df.round(1))` — pandas, not custom HTML.
- Time aggregation: daily for ≤60 days, weekly otherwise.
- Charts: matplotlib + seaborn; `#667eea` and `#764ba2` for bars/lines.
- Progressive rendering: each section gets its own `widgets.Output()`, shows a loading indicator first, then clears and renders. This makes the dashboard feel responsive even on slow queries.
- Landing page with Launch button: instant page load, data only loads on click.

## Iteration & debugging

```bash
# View Voila logs
kubectl logs -n scout-analytics -l app.kubernetes.io/name=voila --tail=200

# Re-publish after edits (overwrites PVC files and ConfigMap entry)
./scripts/scout-publish.sh analytics/notebooks/{playbook-id} --title ... --description ... --icon ... --color ...

# Remove a playbook
./scripts/scout-unpublish.sh {playbook-id}
```

## Files this skill writes

For a playbook with id `my-dashboard`:
```
analytics/notebooks/my-dashboard/
├── MyDashboard.ipynb
└── my_dashboard.py
```

## Example flow

User: "Create a CT volume dashboard"

1. Ask 3-4 clarifying questions (time range default, chart types, grouping by facility vs scanner, icon/color).
2. Read `sample_dashboard.py`.
3. Write `analytics/notebooks/ct-volume/CTVolume.ipynb` and `ct_volume_dashboard.py`, adapting the sample's `_load_data` / `create_dashboard` / `create_landing_page` to the volume use case (drop TAT-specific panels; add per-facility breakdowns).
4. `python -m py_compile analytics/notebooks/ct-volume/ct_volume_dashboard.py`.
5. `./scripts/scout-publish.sh analytics/notebooks/ct-volume --title "CT Volume" --description "Scan volumes over time, by facility" --icon chart --color amber`.
6. Report the playbook URL from the script's output.
