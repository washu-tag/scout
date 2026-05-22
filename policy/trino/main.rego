package trino

# Scout RBAC policy for Trino, evaluated by Trino's `opa` access-control plugin.
#
# Endpoints queried by Trino:
#   POST /v1/data/trino/allow             -> bool       (is the operation permitted?)
#   POST /v1/data/trino/rowFilters        -> [{expression}]  (WHERE clauses AND-merged)
#   POST /v1/data/trino/batchColumnMasks  -> [{index, viewExpression}]  (per-column mask)
#
# Per-user attributes (`allowed_facilities`, `mask_phi_fields`, ...) live in
# OPA's data document under `data.users.<username>`, populated by an OPA
# bundle that the Keycloak SPI listener publishes to MinIO (see ADR 0021).
# OPA's bundle plugin pulls every 5–10s, so a Keycloak change converges to
# all replicas within one pull interval — no per-decision Keycloak call,
# no admin-API client, no cache-busting trick.
#
# Data document (rendered by the opa Ansible role from inventory + bundle):
#   data.filtered_tables       : [{catalog, schema, table}]      # static (inventory)
#   data.view_only_tables      : [{catalog, schema, table}]      # static (inventory)
#   data.view_owner_principals : [identity_name]                 # static (inventory)
#   data.attribute_filters     : {<attr_name>: {column, tables?}} # static (inventory)
#   data.masked_columns        : [column_name]                   # static (inventory)
#   data.users.<username>      : {
#                                  "enabled": bool,
#                                  "allowed_facilities": [...],
#                                  "allowed_modalities": [...],
#                                  "mask_phi_fields": ["true"|"false"],
#                                  ...
#                                }                               # bundled (Keycloak)
# Attribute values mirror Keycloak's `Map<String, List<String>>` user-
# attribute shape (multivalued by default) so the listener writes them
# verbatim — no per-key schema in two places.

import rego.v1

# === Identity =================================================================

identity_present if input.context.identity.user != ""

default user_groups := []
user_groups := input.context.identity.groups

# === Per-user attributes (from OPA bundle) ====================================
# `data.users[user]` returns the bundled record if present, otherwise the
# default {} which yields deny-all via the gates below. The default is
# scoped to `{"enabled": false}` so `user_attrs.enabled == false` reads
# correctly even for users absent from the bundle.

default user_attrs := {"enabled": false}

user_attrs := attrs if {
	identity_present
	attrs := data.users[input.context.identity.user]
}

# === System identities exempt from the `enabled` gate =========================
# Two identity classes don't go through Keycloak's user-attribute store:
#   * View-owner principals (`trino`, used by hl7-transformer for DEFINER
#     views) — see the `is_view_owner` carve-out in row filters / masks.
#   * Impersonation service principals (`superset_svc`, `openwebui_mcp_svc`)
#     — only ever issue ImpersonateUser; the impersonated end-user is what
#     subsequent /allow calls evaluate.
# These don't appear in the bundle. Gating /allow on `user_attrs.enabled`
# without a carve-out would deny their queries entirely, so they get an
# explicit exemption from the enabled check.

trino_service_principals := {"superset_svc", "openwebui_mcp_svc"}

is_system_identity if input.context.identity.user in view_owner_principals_set

is_system_identity if input.context.identity.user in trino_service_principals

# `user_enabled` is the deny-on-disabled gate. True for system identities
# (carve-out above) and for end-users whose bundle entry has enabled=true.
# Absent/disabled human users fall through to the default false and are
# denied at /allow before any row filter or mask runs.
default user_enabled := false

user_enabled if is_system_identity

user_enabled if user_attrs.enabled == true

# === Allow rules ==============================================================

default allow := false

# The set of catalogs Scout RBAC scopes. Other catalogs (system, jmx,
# information_schema) are handled separately so Trino UI / Superset
# autocomplete can enumerate metadata.
guarded_catalogs := {"delta"}

allowed_catalogs := guarded_catalogs | {"system", "jmx", "information_schema"}

# Trino's OPA plugin sends a different `input.action.resource` shape per
# operation. The four `allow` rules below mirror those shapes:
#   1. Catalog-level (resource.catalog.name)
#   2. Schema-level (resource.schema.catalogName)
#   3. Table/column-level (resource.table.catalogName)
#   4. Catalog-less (no resource)
# Folding them together with a single rule + nested checks is tempting but
# Rego's undefined-on-missing-path semantics make the current allow rule
# silently never match for ops like FilterCatalogs (which has no `table`).

# Catalog-level ops: AccessCatalog, ShowSchemas, FilterCatalogs.
allow if {
	identity_present
	user_enabled
	input.action.operation in {"AccessCatalog", "ShowSchemas", "FilterCatalogs"}
	input.action.resource.catalog.name in allowed_catalogs
}

# Schema-level ops: ShowTables, FilterSchemas (per-schema call).
allow if {
	identity_present
	user_enabled
	input.action.operation in {"ShowTables", "FilterSchemas"}
	input.action.resource.schema.catalogName in allowed_catalogs
}

# Table-/column-level ops: SELECT-style + per-table/per-column filters.
# Row filtering is layered on by /rowFilters; PHI columns are protected
# by /batchColumnMasks (see below). FilterColumns only affects metadata
# listings (SHOW COLUMNS, information_schema) — Trino expands SELECT *
# at parse time and runs SelectFromColumns with the full list, so
# column-level protection has to be expressed as a mask, not a deny.
#
# `view_only_blocked` excludes tables that are denied for direct access
# (e.g. join-target tables that the row filters can't reach). Hiding
# them here drops them from SHOW TABLES / SHOW COLUMNS and rejects
# direct SELECTs; views over them keep working as long as the view
# was created with SECURITY DEFINER.
allow if {
	identity_present
	user_enabled
	input.action.operation in {
		"SelectFromColumns",
		"ShowColumns",
		"FilterTables",
		"FilterColumns",
	}
	input.action.resource.table.catalogName in allowed_catalogs
	not view_only_blocked
}

# Catalog-less operations: ShowCatalogs, ExecuteQuery without a resource,
# ReadSystemInformation. ImpersonateUser is intentionally excluded — see
# the dedicated rule below.
allow if {
	identity_present
	user_enabled
	input.action.operation in {"ExecuteQuery", "ReadSystemInformation", "ShowCatalogs"}
	not input.action.resource
}

# CreateViewWithSelectFromColumns: Trino calls this when a user queries
# a view to validate that the view's OWNER (definer) has read access to
# the underlying tables. Identity here is the definer, not the invoker.
# Deliberately does NOT consult view_only_blocked — the whole point of
# view_only_tables is "users can't reach this directly, but views over
# it (SECURITY DEFINER) can read it on their behalf." Without this rule,
# any DEFINER view targeting our delta tables fails for every invoker.
allow if {
	identity_present
	user_enabled
	input.action.operation == "CreateViewWithSelectFromColumns"
	input.action.resource.table.catalogName in allowed_catalogs
}

# ImpersonateUser: only the configured Trino service principals may set
# X-Trino-User to override the effective query identity. End users connecting
# directly via JWT (Jupyter) cannot impersonate; only the impersonation-pattern
# clients (Superset, Open WebUI MCP) can. The Trino JWT user-mapping strips
# the Keycloak `service-account-` prefix, so identity.user matches the bare
# client_id here. Deliberately does NOT check user_enabled — service
# principals aren't in data.users.
allow if {
	input.action.operation == "ImpersonateUser"
	input.context.identity.user in trino_service_principals
}

# === Row filters ==============================================================
# Trino calls /rowFilters for every SELECT-style operation, including queries
# against information_schema.* and system.* pseudo-schemas. A filter that
# references a column the target table doesn't have crashes the query with
# COLUMN_NOT_FOUND, breaking Superset autocomplete. Filters therefore only
# fire on tables in data.filtered_tables (per-attribute override available),
# and the 1=0 zero-row clamp is defense-in-depth: /allow may have permitted
# the operation, but without this clamp the user would read the full table.
#
# data shape:
#   data.filtered_tables  = [{catalog, schema, table}, ...]   # shared list
#   data.attribute_filters = {
#     "<keycloak_attribute>": {
#       "column": "<sql_column>",
#       # optional override; defaults to data.filtered_tables
#       "tables": [{"catalog": ..., "schema": ..., "table": ...}]
#     },
#     ...
#   }
# Adding a new restriction dimension is a data update — no rule edit.

# Permissive regex for filter values: alphanumeric + `_` + `-`. Tight enough
# to drop SQL-injection attempts, broad enough for facility codes (WUSM),
# modality codes (CT, MR), and similar short categorical values.
filter_value_pattern := `^[A-Za-z0-9_-]+$`

input_table := {
	"catalog": input.action.resource.table.catalogName,
	"schema": input.action.resource.table.schemaName,
	"table": input.action.resource.table.tableName,
}

# Tables the policy denies for direct access. Loaded with a default so
# the rule below can reference it even when the inventory omits it.
default view_only_tables_set := set()
view_only_tables_set := {t | some t in data.view_only_tables}

view_only_blocked if input_table in view_only_tables_set

# View-owner service principals (e.g. the `trino` admin used by
# hl7-transformer to CREATE VIEW). When an invoker queries a DEFINER
# view, Trino evaluates the underlying-table reads as the view owner —
# row filters and column masks scoped to the owner would clamp the
# view's reads to zero rows. Owners listed here bypass row-filter and
# column-mask rules; the invoker's RBAC is still enforced at the view
# level (views are in filtered_tables).
default view_owner_principals_set := set()
view_owner_principals_set := {p | some p in data.view_owner_principals}

is_view_owner if input.context.identity.user in view_owner_principals_set

# Per-attribute user values, defaulting to [] if the attribute is unset.
user_values_for(attr_name) := values if {
	values := user_attrs[attr_name]
} else := []

# Wildcard `*` in a user's values for an attribute disables the filter
# for that attribute (admin-style "see everything for this dimension").
has_wildcard(attr_name) if "*" in user_values_for(attr_name)

# Tables an attribute's filter applies to: explicit override on the
# attribute entry, otherwise the global filtered_tables list.
attribute_tables(config) := object.get(config, "tables", data.filtered_tables)

attribute_scopes_table(config) if {
	some t in attribute_tables(config)
	t.catalog == input_table.catalog
	t.schema == input_table.schema
	t.table == input_table.table
}

# Emit `column IN ('v1','v2',...)` for each configured attribute the user
# has valid non-wildcard values for, scoped to that attribute's tables.
rowFilters contains {"expression": expr} if {
	not is_view_owner
	some attr_name, config in data.attribute_filters
	attribute_scopes_table(config)
	not has_wildcard(attr_name)
	values := user_values_for(attr_name)
	valid := [v | v := values[_]; regex.match(filter_value_pattern, v)]
	count(valid) > 0
	quoted := [sprintf("'%s'", [v]) | v := valid[_]]
	expr := sprintf("%s IN (%s)", [config.column, concat(",", quoted)])
}

# 1=0 zero-row clamp: any configured attribute scoping this table where
# the user has neither wildcard nor any valid value clamps the query to
# zero rows. Multiple attributes AND together (Trino combines rowFilters
# with AND), so missing values on any one dimension blocks all rows.
rowFilters contains {"expression": "1=0"} if {
	not is_view_owner
	some attr_name, config in data.attribute_filters
	attribute_scopes_table(config)
	not has_wildcard(attr_name)
	values := user_values_for(attr_name)
	count([v | v := values[_]; regex.match(filter_value_pattern, v)]) == 0
}

# === Column masks (batch mode) ================================================
# Trino calls /batchColumnMasks once per query, batching every projected column
# under input.action.filterResources. Items in the returned set are keyed by
# index into that array; columns omitted from the set are returned unchanged.
# Batch mode amortizes the OPA roundtrip across the whole projection.

default masked_columns := set()
masked_columns := {c | some c in data.masked_columns}

# Keycloak returns user attribute values as arrays (multivalued by default).
# `mask_phi_fields` is single-valued logically but lives in attributes[0].
# Default-mask: unset, missing, or any value other than "false" -> mask.
should_mask if {
	val := object.get(user_attrs, "mask_phi_fields", ["true"])
	count(val) > 0
	val[0] != "false"
}

# Mask expression dispatches on column type: varchar columns get the
# literal '[REDACTED]' so the user sees something explicit was redacted;
# anything else (array, row, decimal, timestamp, ...) gets bare NULL
# and relies on Trino's analyzer to coerce it to the column's declared
# type. Bare NULL avoids reflecting Trino's serialized type string back
# into a CAST expression.
mask_expression(column) := "'[REDACTED]'" if {
	startswith(column.columnType, "varchar")
}

mask_expression(column) := "NULL" if {
	not startswith(column.columnType, "varchar")
}

batchColumnMasks contains {"index": i, "viewExpression": {"expression": expr}} if {
	not is_view_owner
	should_mask
	some i, resource in input.action.filterResources
	resource.column.catalogName == "delta"
	resource.column.columnName in masked_columns
	expr := mask_expression(resource.column)
}

# === Decision context (audit log readability) =================================
# Surfaced under data.trino.decision_context so OPA's decision_logs include the
# computed view (user, attribute values, etc.) alongside the bare allow/deny.

# Per-attribute snapshot for audit: one entry per configured attribute_filters
# key, with the user's effective values for that dimension.
attribute_snapshot[attr_name] := values if {
	some attr_name, _ in data.attribute_filters
	values := user_values_for(attr_name)
}

decision_context := {
	"user": input.context.identity.user,
	"groups": user_groups,
	"enabled": user_enabled,
	"attribute_values": attribute_snapshot,
	"mask_phi_fields": object.get(user_attrs, "mask_phi_fields", ["unset"]),
	"row_filters": rowFilters,
}
