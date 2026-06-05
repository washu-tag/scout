# Configuring Data Authorization

This page is for Scout administrators. The per-user row filtering and column masking described in [Data Authorization](../data_authorization.md) are configured as attributes on each user's Keycloak account.

Scout's authorization model is **attribute-driven**, not group-driven. Permissions are stored as attributes on each user's Keycloak account; you set them once per user and they take effect for every Trino-backed surface — Analytics, Notebooks, Chat.

## The shipped attributes

Set per-user in the Keycloak admin console, in the **scout** realm (Scout's users live there, not in the default **master** realm), under **Users → \[user\] → Attributes**. All live in the **scout** attribute group.

| Attribute | Type | Default if unset | Effect |
|---|---|---|---|
| `allowed_facilities` | multivalued (codes or `*`) | empty → no rows | Row filter on `sending_facility`. Multiple values OR together. `*` is a wildcard. |
| `mask_phi_fields` | single, `"true"` or `"false"` | `"true"` (mask) | Toggles PHI column masking. Set to `"false"` only for users authorized to see PHI in the clear. |
| `bypass_hidden_tables` | single, `"true"` or `"false"` | `"false"` (block) | Lets the user `SELECT` directly from join-target tables (patient mapping). See [View-only tables](#view-only-tables) below. |

```{important}
Empty `allowed_facilities` means **deny-all rows**, not "see everything." Newly approved users have no attributes set and will see zero rows from filtered tables until an admin grants them values. Use `*` to grant "see all facilities." The same deny-by-default rule applies to any additional row-filter dimensions a deployment adds (see [Adding a new restriction dimension](#adding-a-new-restriction-dimension)).
```

## Setting attributes — example walkthrough

A new user needs to query reports from the HOSP1 facility, with PHI columns visible:

1. Open the Keycloak admin console (`https://keycloak.<your-scout-host>/admin`)
2. Switch to the **scout** realm (realm selector, top-left — the console opens in **master** by default)
3. **Users → \[the user\]** → **Attributes** tab
4. Add:
   - `allowed_facilities`: `HOSP1`
   - `mask_phi_fields`: `false`
5. **Save**

Within 5-15 seconds, the user's next Analytics query will reflect the new permissions. No restart, no logout, no cache clear required.

(how-propagation-works)=
## How propagation works

Scout's policy engine ([OPA](https://www.openpolicyagent.org/)) doesn't query Keycloak at decision time — it consumes a periodically refreshed snapshot of all users' attributes. When you save an attribute change in Keycloak, the in-cluster listener picks up the admin event, republishes the user-attribute bundle within ~1 second, and OPA's bundle plugin re-pulls within its 5-10s polling interval. Total propagation: **5-15 seconds**.

This means user disable (toggling **Enabled** off on the user) also propagates within seconds — a disabled user's next Trino query is denied. See ADR 0021 in the internal docs for the implementation.

## Verifying what a user sees — `decision_context`

If a user reports unexpected query results, you can ask OPA directly — via its [REST Data API](https://www.openpolicyagent.org/docs/latest/rest-api/) — what it computed for that user against a specific table. The `decision_context` endpoint returns the user's attributes, computed row filters, and computed mask state in a single response.

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
    "allowed_facilities": ["HOSP1"]
  },
  "mask_phi_fields": ["true"],
  "bypass_hidden_tables": false,
  "row_filters": [
    {"expression": "sending_facility IN ('HOSP1')"}
  ]
}
```

The full response also includes `approved` (approval-group membership) and `bundle_groups` (the groups OPA received in its attribute bundle), omitted above for brevity. Pass a different `tableName` to see the filters that would apply to a different table. Unfiltered tables (anything not in the inventory's `trino_filtered_tables` list) return an empty `row_filters` array.

## Troubleshooting

**"I see zero rows from `reports_latest`."**
Run `decision_context` for the user. If `row_filters` includes `1=0`, the user is missing values for one of the configured attribute dimensions (`allowed_facilities` by default, plus whatever your deployment has added via `trino_attribute_filters`). Either the user has no values set, or the configured values don't match the regex `^[A-Za-z0-9_-]+$` (rare — usually caused by a space, comma, or other unsupported character).

**"I see `[REDACTED]` everywhere; I should be able to see PHI."**
Check the user's `mask_phi_fields` attribute. Default behavior is to mask, so absence of the attribute = masking on. Set explicitly to `"false"` (lowercase, as a string) to disable masking.

**"`SELECT * FROM reports_report_patient_mapping` says permission denied."**
The patient mapping table and its history variant are blocked from direct access for everyone — they let a facility-scoped user enumerate cross-facility patient identifiers, defeating the row-filter restriction. Use one of the `*_epic_view` join views instead (`reports_curated_epic_view`, `reports_latest_epic_view`, `reports_dx_epic_view`); these expose mapping data filtered to the invoker's permitted facilities. See [View-only tables](#view-only-tables) below if a specific admin user does need direct access.

**"The user logged in, but their permission changes haven't taken effect."**
Attribute changes propagate on the bundle-refresh cycle (5-15 seconds; see [How propagation works](#how-propagation-works)), and Trino re-evaluates authorization for every query at planning time — there is no per-connection decision cache. The next query the user runs after propagation reflects the new attributes, with no logout or reconnect required. If results still look stale after ~15 seconds, confirm the change saved in Keycloak and run `decision_context` for the user.

(view-only-tables)=
## View-only tables

A small number of tables in the data lake are designated **view-only**: direct `SELECT` is denied for all users, but `SECURITY DEFINER` views over them work normally. Today these are:

* `reports_report_patient_mapping` — the canonical cross-facility patient-ID resolution table.
* `reports_report_patient_mapping_history` — the historical record of patient-ID changes.

Why? These tables key on `scout_patient_id` and carry cross-facility identifiers (`mpi`, `epic_mrn`, etc.). A user restricted to one facility could otherwise `SELECT` directly from them and enumerate every patient ever resolved across all facilities — defeating the row-filter design.

The joined `*_epic_view` views (created by the HL7 transformer with `SECURITY DEFINER`) read the underlying mapping table as the view's owner and then apply the invoker's row filters to the output. Use these views for any cross-reference query — they cover the legitimate use cases without exposing the raw join target.

For administrators who legitimately need direct mapping-table access (e.g., for cross-facility patient-ID reconciliation), set `bypass_hidden_tables` to `"true"` on that specific user. Use sparingly — every user with this attribute can enumerate the full cross-facility patient population.

(adding-a-new-restriction-dimension)=
## Adding a new restriction dimension

The set of row-filter dimensions is configured per-deployment in the Ansible inventory under `trino_attribute_filters` (default: `allowed_facilities` only). Adding a new dimension (e.g., `allowed_modalities` → `modality`, or `allowed_departments`) is a one-line inventory edit; the Keycloak realm template auto-renders the new user-profile attribute, and the rego policy auto-fires the new row filter. See [Inventory](inventory.md) for the configuration shape.

(configuring-masked-columns)=
## Configuring which columns are masked

Which columns count as PHI is configured per-deployment in the Ansible inventory under `trino_masked_columns` (default: `patient_name`, `full_patient_name`, `zip_or_postal_code`). Adding a column is a one-line inventory edit — no policy change. The mask applies to that column name across every `delta` table that projects it, for any user whose `mask_phi_fields` is unset or `"true"`. `varchar` columns render as `[REDACTED]`; other types (arrays, rows, decimals, timestamps) render as `NULL`. The per-user `mask_phi_fields` attribute toggles whether this masking applies to a given user; `trino_masked_columns` controls *which* columns it covers.

## Filtering a family of tables by prefix

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

* [Data Authorization](../data_authorization.md) — the end-user view of these restrictions
* [Inventory](inventory.md) — Ansible configuration for per-deployment filter dimensions and view-only tables
* [Data Schema](../dataschema.md) — column-level details of the report tables
