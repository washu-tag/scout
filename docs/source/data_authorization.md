# Data Authorization

Scout authorizes access to report data on a per-user basis. Authentication (who you are — covered in [Authentication](authentication.md)) is separate from authorization (what data you can see, covered here). Two kinds of restriction are applied at the query layer (Trino, used by Analytics, Notebooks, Chat):

* **Row filtering** — you only see rows whose `sending_facility` matches attribute values configured on your user account. A user whose `allowed_facilities` is set to `["WUSM"]` sees only WUSM rows in `reports`, `reports_curated`, `reports_latest`, `reports_dx`, and the joined `*_epic_view` views. (Additional row-filter dimensions can be added per deployment — see [Adding a new restriction dimension](#adding-a-new-restriction-dimension) below.)
* **Column masking** — protected health information (PHI) columns (`patient_name`, `full_patient_name`, `zip_or_postal_code`) are returned as `[REDACTED]` (for varchar columns) or `NULL` (for complex types). Whether masking applies is controlled by your user's `mask_phi_fields` attribute.

```{note}
If you query a report table and see far fewer rows than expected, or see `[REDACTED]` where you expected a name, that's data authorization at work. Your account's attribute values are being applied as configured. Contact your Scout administrator if the configuration looks wrong.
```

## For Scout administrators

Scout's authorization model is **attribute-driven**, not group-driven. Permissions are stored as attributes on each user's Keycloak account; you set them once per user (or via Keycloak's user-federation flow if you're piping in from an upstream IdP) and they take effect for every Trino-backed surface — Analytics, Notebooks, Chat.

### The shipped attributes

Set per-user in the Keycloak admin console under **Users → \[user\] → Attributes**. All live in the **scout** attribute group.

| Attribute | Type | Default if unset | Effect |
|---|---|---|---|
| `allowed_facilities` | multivalued (codes or `*`) | empty → no rows | Row filter on `sending_facility`. Multiple values OR together. `*` is a wildcard. |
| `mask_phi_fields` | single, `"true"` or `"false"` | `"true"` (mask) | Toggles PHI column masking. Set to `"false"` only for users authorized to see PHI in the clear. |
| `bypass_hidden_tables` | single, `"true"` or `"false"` | `"false"` (block) | Lets the user `SELECT` directly from join-target tables (patient mapping). See [View-only tables](#view-only-tables) below. |

```{important}
Empty `allowed_facilities` means **deny-all rows**, not "see everything." Newly approved users have no attributes set and will see zero rows from filtered tables until an admin grants them values. Use `*` to grant "see all facilities." The same deny-by-default rule applies to any additional row-filter dimensions a deployment adds (see [Adding a new restriction dimension](#adding-a-new-restriction-dimension)).
```

### Setting attributes — example walkthrough

A new user joined the WUSM site and needs to query reports from the BJH facility, with PHI columns visible:

1. Open the Keycloak admin console (`https://keycloak.<your-scout-host>/admin`)
2. **Users → \[the user\]** → **Attributes** tab
3. Add:
   - `allowed_facilities`: `BJH`
   - `mask_phi_fields`: `false`
4. **Save**

Within 5-10 seconds, the user's next Analytics query will reflect the new permissions. No restart, no logout, no cache clear required.

### How propagation works

Scout's policy engine (OPA) doesn't query Keycloak at decision time — it consumes a periodically refreshed snapshot of all users' attributes. When you save an attribute change in Keycloak, the in-cluster listener picks up the admin event, republishes the user-attribute bundle within ~1 second, and OPA's bundle plugin re-pulls within its 5-10s polling interval. Total propagation: **5-15 seconds**.

This means user disable (toggling **Enabled** off on the user) also propagates within seconds — a disabled user's next Trino query is denied. See ADR 0021 in the internal docs for the implementation.

### Verifying what a user sees — `decision_context`

If a user reports unexpected query results, you can ask OPA directly what it computed for that user against a specific table. The `decision_context` endpoint returns the user's attributes, computed row filters, and computed mask state in a single response.

From a workstation with cluster access:

```bash
kubectl -n scout-analytics port-forward svc/opa-trino 8181:8181 &

curl -sX POST http://localhost:8181/v1/data/trino/decision_context \
  -H 'Content-Type: application/json' \
  -d '{
    "input": {
      "action": {
        "operation": "SelectFromColumns",
        "resource": {"table": {
          "catalogName": "delta",
          "schemaName": "default",
          "tableName": "reports_latest"
        }}
      },
      "context": {"identity": {"user": "<USERNAME>", "groups": []}}
    }
  }' | jq .result
```

Example output:

```json
{
  "user": "alice",
  "enabled": true,
  "groups": [],
  "attribute_values": {
    "allowed_facilities": ["WUSM"]
  },
  "mask_phi_fields": ["true"],
  "bypass_hidden_tables": false,
  "row_filters": [
    {"expression": "sending_facility IN ('WUSM')"}
  ]
}
```

Pass a different `tableName` to see the filters that would apply to a different table. Unfiltered tables (anything not in the inventory's `trino_filtered_tables` list) return an empty `row_filters` array.

### Troubleshooting

**"I see zero rows from `reports_latest`."**
Run `decision_context` for the user. If `row_filters` includes `1=0`, the user is missing values for one of the configured attribute dimensions (`allowed_facilities` by default, plus whatever your deployment has added via `trino_attribute_filters`). Either the user has no values set, or the configured values don't match the regex `^[A-Za-z0-9_-]+$` (rare — usually caused by a space, comma, or other unsupported character).

**"I see `[REDACTED]` everywhere; I should be able to see PHI."**
Check the user's `mask_phi_fields` attribute. Default behavior is to mask, so absence of the attribute = masking on. Set explicitly to `"false"` (lowercase, as a string) to disable masking.

**"`SELECT * FROM reports_report_patient_mapping` says permission denied."**
The patient mapping table and its history variant are blocked from direct access for everyone — they let a facility-scoped user enumerate cross-facility patient identifiers, defeating the row-filter restriction. Use one of the `*_epic_view` join views instead (`reports_curated_epic_view`, `reports_latest_epic_view`, `reports_dx_epic_view`); these expose mapping data filtered to the invoker's permitted facilities. See [View-only tables](#view-only-tables) below if a specific admin user does need direct access.

**"The user logged in, but their permission changes haven't taken effect."**
Trino has its own connection pool that may cache decisions briefly per active connection. New queries on a new connection always reflect the latest attributes. If a session-pooled tool (Superset SQL Lab, Notebook with an active Trino connection) is showing stale behavior, opening a new query window forces a fresh connection.

(view-only-tables)=
### View-only tables

A small number of tables in the data lake are designated **view-only**: direct `SELECT` is denied for all users, but `SECURITY DEFINER` views over them work normally. Today these are:

* `reports_report_patient_mapping` — the canonical cross-facility patient-ID resolution table.
* `reports_report_patient_mapping_history` — the historical record of patient-ID changes.

Why? These tables key on `scout_patient_id` and carry cross-facility identifiers (`mpi`, `epic_mrn`, etc.). A user restricted to one facility could otherwise `SELECT` directly from them and enumerate every patient ever resolved across all facilities — defeating the row-filter design.

The joined `*_epic_view` views (created by the HL7 transformer with `SECURITY DEFINER`) read the underlying mapping table as the view's owner and then apply the invoker's row filters to the output. Use these views for any cross-reference query — they cover the legitimate use cases without exposing the raw join target.

For administrators who legitimately need direct mapping-table access (e.g., for cross-facility patient-ID reconciliation), set `bypass_hidden_tables: true` on that specific user. Use sparingly — every user with this attribute can enumerate the full cross-facility patient population.

### Adding a new restriction dimension

The set of row-filter dimensions is configured per-deployment in the Ansible inventory under `trino_attribute_filters` (default: `allowed_facilities` only). Adding a new dimension (e.g., `allowed_modalities` → `modality`, or `allowed_departments`) is a one-line inventory edit; the Keycloak realm template auto-renders the new user-profile attribute, and the rego policy auto-fires the new row filter. See [Inventory](technical/inventory.md) for the configuration shape.

### Filtering a family of tables by prefix

`trino_filtered_tables` and `trino_hidden_tables` entries accept either an exact table name or a `table_prefix` that matches every table in the catalog/schema whose name starts with that prefix:

```yaml
trino_filtered_tables:
  - { catalog: delta, schema: default, table_prefix: reports_ }  # row filter applies to every reports_* table

trino_hidden_tables:
  - { catalog: delta, schema: default, table: reports_report_patient_mapping }
  - { catalog: delta, schema: default, table: reports_report_patient_mapping_history }
```

Useful when the transformer adds new derivative tables sharing a naming convention — one prefix covers the whole family instead of an inventory edit per table.

**Caveat for `trino_filtered_tables`:** the row filter emits `<column> IN (...)` based on `trino_attribute_filters`, so every table matched by the prefix must have those columns or queries error with `COLUMN_NOT_FOUND`. Tables that match the prefix but are also in `trino_hidden_tables` are automatically excluded from row-filter scoping (no filter is emitted for them) — that's the safety latch keeping `reports_report_patient_mapping` correct under a `reports_` prefix.

## See also

* [Authentication](authentication.md) — how users log in (separate concern from what they can see)
* [Data Schema](dataschema.md) — column-level details of the report tables that row filtering and column masking operate on
* [Inventory](technical/inventory.md) — Ansible configuration for per-deployment filter dimensions and view-only tables
