package trino

# Scout AuthZ policy for Trino, evaluated by Trino's `opa` access-control plugin.
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
#   data.filtered_tables       : [{catalog, schema, table|table_prefix}]
#                                                                # static (inventory)
#   data.hidden_tables      : [{catalog, schema, table|table_prefix}]
#                                # site-specific additions only (inventory);
#                                # baseline patient-mapping entries are
#                                # hardcoded below in `baseline_hidden_tables`
#   data.view_owner_principals : [identity_name]                 # static (inventory)
#   data.attribute_filters     : {<attr_name>: {column, tables?}} # static (inventory)
#   data.masked_columns        : [column_name]                   # static (inventory)
#   data.users.<username>      : {
#                                  "enabled": bool,
#                                  "groups": [group_name, ...],
#                                  "allowed_facilities": [...],
#                                  "allowed_modalities": [...],
#                                  "mask_phi_fields": ["true"|"false"],
#                                  ...
#                                }                               # bundled (Keycloak)
# Table-list entries support two shapes — exact `table` match or
# `table_prefix` startswith match. See table_entry_matches.
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
#   * Impersonation service principals (`superset_svc`, `voila_svc`,
#     `report_viewer_svc`). These only ever issue ImpersonateUser; the
#     impersonated end-user is what subsequent /allow calls evaluate.
# These don't appear in the bundle. Gating /allow on `user_attrs.enabled`
# without a carve-out would deny their queries entirely, so they get an
# explicit exemption from the enabled check.

trino_service_principals := {"superset_svc", "voila_svc", "report_viewer_svc"}

is_system_identity if input.context.identity.user in view_owner_principals_set

is_system_identity if input.context.identity.user in trino_service_principals

# `user_enabled` is the approval+enabled gate. True for system identities
# (carve-out above) and for end-users whose bundle entry has both
# enabled=true AND membership in one of the approved groups below. The
# group check exists because Keycloak federates from upstream IdPs — a
# user can land in the realm with enabled=true purely from a successful
# upstream OIDC login, before going through Scout's approval flow that
# adds them to scout-user. The approved-group check catches that gap.
# Absent / disabled / unapproved users fall through to the default false
# and are denied at /allow before any row filter or mask runs.
#
# `approved_groups` is hardcoded here because the same group names are
# hardcoded in the Keycloak realm template
# (ansible/roles/keycloak/templates/scout-realm.json.j2). Both sides are
# managed by the same team; if the group set ever needs to expand it's
# one rego edit + one realm-template edit, not worth the inventory
# plumbing.
approved_groups := {"scout-user", "scout-admin"}

user_in_approved_group if {
	some g in user_attrs.groups
	g in approved_groups
}

default user_enabled := false

user_enabled if is_system_identity

user_enabled if {
	user_attrs.enabled == true
	user_in_approved_group
}

# === Allow rules ==============================================================

default allow := false

# The set of catalogs Scout AuthZ scopes. Other catalogs (system, jmx,
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
# `hidden_table_blocked` excludes tables that are denied for direct access
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
	not hidden_table_blocked
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
# Deliberately does NOT consult hidden_table_blocked — the whole point of
# hidden_tables is "users can't reach this directly, but views over
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
# clients (Superset, Voila, report-viewer) can. The Trino JWT user-mapping
# strips the Keycloak `service-account-` prefix, so identity.user matches the
# bare client_id here. Deliberately does NOT check user_enabled — service
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

# Permissive regex for filter values: alphanumeric, space, `_`, `-`. Tight enough
# to drop SQL-injection attempts — quotes, semicolons, parens, anything that could
# break out of the single-quoted IN list — broad enough for facility codes (WUSM),
# modality codes (CT, MR), and multi-word facility names like "HOME CARE SERVICES".
# A space is safe: the value is always single-quoted, so it can't break out.
filter_value_pattern := `^[A-Za-z0-9 _-]+$`

input_table := {
	"catalog": input.action.resource.table.catalogName,
	"schema": input.action.resource.table.schemaName,
	"table": input.action.resource.table.tableName,
}

# Match a table-list entry against the current decision's input_table.
# Two shapes are accepted; an entry can use either field but not both.
#   { catalog, schema, table }         — exact match
#   { catalog, schema, table_prefix }  — startswith match
# Prefix matching lets an operator cover a family of tables sharing a
# naming convention (e.g. all `reports_*` derivatives) without
# enumerating each one. The trade-off is that prefix entries can
# over-match — see attribute_scopes_table for the explicit
# hidden_tables exclusion that keeps the safety property obvious
# instead of layered.
table_entry_matches(entry, in_table) if {
	entry.catalog == in_table.catalog
	entry.schema == in_table.schema
	entry.table == in_table.table
}

table_entry_matches(entry, in_table) if {
	entry.catalog == in_table.catalog
	entry.schema == in_table.schema
	entry.table_prefix
	startswith(in_table.table, entry.table_prefix)
}

# Baseline hidden tables: the patient mapping pair written by the
# hl7-transformer. Hardcoded here (not inventory-driven) because they're
# intrinsic to Scout's data lake shape — every deployment has them, and
# they carry cross-facility identifiers keyed on scout_patient_id rather
# than sending_facility, so row filters can't constrain them. Direct
# SELECT must be denied; removing the protection would be a security
# regression. If the hl7-transformer renames these, update this list
# (same-team coupling, same pattern as approved_groups above).
#
# data.hidden_tables (inventory) is unioned on top for site-specific
# additions (e.g., a partner's custom cross-facility join table).
baseline_hidden_tables := [
	{"catalog": "delta", "schema": "default", "table": "reports_report_patient_mapping"},
	{"catalog": "delta", "schema": "default", "table": "reports_report_patient_mapping_history"},
]

# True if the input table matches any baseline or inventory-added
# hidden-table entry (either shape — exact or prefix). Used both by
# hidden_table_blocked (the /allow gate) and by attribute_scopes_table
# (to exclude these tables from row-filter scoping; see the comment there).
# Two rules — `is_hidden_table` is the OR — so the baseline still applies
# even if data.hidden_tables is undefined or empty in a given environment.
is_hidden_table if {
	some t in baseline_hidden_tables
	table_entry_matches(t, input_table)
}

is_hidden_table if {
	some t in data.hidden_tables
	table_entry_matches(t, input_table)
}

# Per-user escape hatch: setting Keycloak attribute
# `bypass_hidden_tables: ["true"]` exempts the user from the
# table-level deny. Used by site operators who legitimately need full
# cross-facility introspection (e.g., the canonical "list all
# scout_patient_ids for epic_mrn X" query). Default is the block-
# everyone behavior — bypass requires an explicit attribute set by
# an admin. Service principals don't carry user_attrs so the default
# `["false"]` applies; the impersonation flow uses the impersonated
# end-user's attrs, so bypass is keyed on the human user, not the
# Trino client.
hidden_table_bypass if {
	val := object.get(user_attrs, "bypass_hidden_tables", ["false"])
	count(val) > 0
	val[0] == "true"
}

hidden_table_blocked if {
	is_hidden_table
	not hidden_table_bypass
}

# View-owner service principals (e.g. the `trino` admin used by
# hl7-transformer to CREATE VIEW). When an invoker queries a DEFINER
# view, Trino evaluates the underlying-table reads as the view owner —
# row filters and column masks scoped to the owner would clamp the
# view's reads to zero rows. Owners listed here bypass row-filter and
# column-mask rules; the invoker's AuthZ is still enforced at the view
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
# attribute entry, otherwise the global filtered_tables list. Each
# entry uses the {catalog, schema, table|table_prefix} shape resolved
# by table_entry_matches.
attribute_tables(config) := object.get(config, "tables", data.filtered_tables)

attribute_scopes_table(config) if {
	some t in attribute_tables(config)
	table_entry_matches(t, input_table)
	# Tables locked down for direct access (data.hidden_tables) are
	# excluded from row-filter scoping even when a prefix entry would
	# otherwise catch them. Direct queries to view-only tables are
	# denied at /allow before /rowFilters runs; DEFINER-view paths
	# already skip row filters via the is_view_owner carve-out. The
	# explicit exclusion here makes that safety visible in the rule
	# instead of dependent on layered behavior the reader has to
	# trace through three other rules. Without it, a prefix like
	# `reports_` would emit a `sending_facility IN (...)` filter for
	# `reports_report_patient_mapping`, which lacks that column —
	# harmless today but a latent bug if the access-path interceptors
	# ever change.
	not is_hidden_table
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
# Default-mask: PHI is masked unless the attribute is explicitly ["false"].
# Unset, missing, empty list, or any other value masks (fail-safe for PHI).
# We test the disable condition and negate it rather than testing val[0]
# directly: a present-but-empty [] makes a positive val[0] check fail, which
# would silently skip masking — the opposite of the fail-safe we want.
should_mask if not phi_masking_disabled

phi_masking_disabled if user_attrs.mask_phi_fields[0] == "false"

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
	"bundle_groups": object.get(user_attrs, "groups", []),
	"approved": user_in_approved_group,
	"enabled": user_enabled,
	"attribute_values": attribute_snapshot,
	"mask_phi_fields": object.get(user_attrs, "mask_phi_fields", ["unset"]),
	"bypass_hidden_tables": hidden_table_bypass,
	"row_filters": rowFilters,
}
