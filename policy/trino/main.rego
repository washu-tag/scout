package trino

# Scout RBAC policy for Trino, evaluated by Trino's `opa` access-control plugin.
#
# Endpoints queried by Trino:
#   POST /v1/data/trino/allow             -> bool       (is the operation permitted?)
#   POST /v1/data/trino/rowFilters        -> [{expression}]  (WHERE clauses AND-merged)
#   POST /v1/data/trino/batchColumnMasks  -> [{index, viewExpression}]  (per-column mask)
#
# Per-user attributes (`allowed_facilities`, `mask_phi_fields`) come from
# Keycloak via http.send. Trino's OPA plugin only forwards identity.user +
# identity.groups in `input.context.identity` — no JWT claims, no extra
# credentials — so Rego must fetch attributes on its own.
#
# Data document (rendered by the opa Ansible role from inventory):
#   data.row_filter_tables.sending_facility : [{catalog, schema, table}]
#   data.masked_columns                     : [column_name]
#   data.keycloak.url                       : "http://keycloak.scout-core.svc:8080"
#   data.keycloak.realm                     : "scout"
#   data.keycloak.reader_client_id          : "opa-keycloak-reader"
# The matching client secret is mounted into OPA as env var
# KEYCLOAK_READER_CLIENT_SECRET (read via opa.runtime().env at policy time).

import rego.v1

# === Identity =================================================================

identity_present if input.context.identity.user != ""

default user_groups := []
user_groups := input.context.identity.groups

# === Keycloak attribute fetch =================================================
# http.send is cached by request body; the cache_duration here is the
# steady-state floor for staleness. Real-time invalidation is layered on
# top via the OpaInvalidationEventListener SPI in keycloak/event-listener:
# when an admin updates or deletes a user, the listener PUTs a fresh
# timestamp to /v1/data/keycloak_invalidations/<username> on each
# configured OPA replica. The timestamp is threaded into the http.send
# URL below as an `_inv` query param — Keycloak ignores unknown params,
# but OPA's cache key is the full request, so any new value forces a
# fresh fetch on the next decision. Absent a push, attrs may be up to
# force_cache_duration_seconds stale.

# Per-user cache-busting timestamp written by the Keycloak event
# listener. Defaults to 0 when no listener has fired for this user
# (which includes pre-listener-deploy, when the data document doesn't
# have a `keycloak_invalidations` key at all). The path is written as
# a specific reference (not `object.get(data, ...)`) so the Rego
# compiler treats `data.keycloak_invalidations` as a single virtual
# document — using `data` as a whole would force OPA to see every
# package as a transitive dep and the test suite would fail with
# recursion errors.
default user_invalidation_ts := 0

user_invalidation_ts := ts if {
	identity_present
	ts := data.keycloak_invalidations[input.context.identity.user]
}

default user_attrs := {}

user_attrs := attrs if {
	identity_present
	token := _keycloak_admin_token
	token != ""
	response := http.send({
		"method": "GET",
		"url": sprintf(
			"%s/admin/realms/%s/users?username=%s&exact=true&_inv=%v",
			[data.keycloak.url, data.keycloak.realm, input.context.identity.user, user_invalidation_ts],
		),
		"headers": {"Authorization": sprintf("Bearer %s", [token])},
		"cache": true,
		"force_cache": true,
		"force_cache_duration_seconds": 60,
	})
	response.status_code == 200
	count(response.body) > 0
	attrs := object.get(response.body[0], "attributes", {})
}

# === Keycloak admin token =====================================================
# The JWT `exp` claim is the authoritative validity check — the cache TTL
# is a performance knob, not a correctness gate. With Keycloak's typical
# 300s access-token lifespan and the short cache TTL below, a cached token
# is refreshed well before its exp, so `_token_still_valid` rarely rejects
# in practice. If it ever does (clock skew above the safety margin, or
# Keycloak issues a short-lived token), the rule fails and `user_attrs`
# falls back to its default `{}` — yielding a deny-all row filter until
# the cache refreshes on the next request after TTL.

# Safety margin (seconds) deducted from the JWT exp claim before deciding
# the token is still usable. Covers clock skew between OPA and Keycloak
# (sub-second in-cluster) plus the time between this check and the
# subsequent http.send to Keycloak's user endpoint.
_token_safety_margin_seconds := 30

# Inter-query cache TTL. Performance knob only — JWT exp is the validity
# gate. Short enough that the cached token stays far from expiry under any
# reasonable Keycloak lifespan; long enough to amortize round-trips within
# a single user's burst of queries.
_token_cache_ttl_seconds := 60

default _keycloak_admin_token := ""

_keycloak_admin_token := token if {
	response := http.send({
		"method": "POST",
		"url": sprintf(
			"%s/realms/%s/protocol/openid-connect/token",
			[data.keycloak.url, data.keycloak.realm],
		),
		"headers": {"Content-Type": "application/x-www-form-urlencoded"},
		"raw_body": sprintf(
			"grant_type=client_credentials&client_id=%s&client_secret=%s",
			[data.keycloak.reader_client_id, opa.runtime().env.KEYCLOAK_READER_CLIENT_SECRET],
		),
		"cache": true,
		"force_cache": true,
		"force_cache_duration_seconds": _token_cache_ttl_seconds,
	})
	response.status_code == 200
	token := response.body.access_token
	_token_still_valid(token)
}

# Token validity is read directly from the JWT's `exp` claim — the
# authoritative source. `io.jwt.decode` returns undefined on a malformed
# token, which causes this rule to fail and is treated as "not valid".
_token_still_valid(token) if {
	[_, payload, _] := io.jwt.decode(token)
	payload.exp > (time.now_ns() / 1000000000) + _token_safety_margin_seconds
}

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
	input.action.operation in {"AccessCatalog", "ShowSchemas", "FilterCatalogs"}
	input.action.resource.catalog.name in allowed_catalogs
}

# Schema-level ops: ShowTables, FilterSchemas (per-schema call).
allow if {
	identity_present
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
	input.action.operation == "CreateViewWithSelectFromColumns"
	input.action.resource.table.catalogName in allowed_catalogs
}

# ImpersonateUser: only the configured Trino service principals may set
# X-Trino-User to override the effective query identity. End users connecting
# directly via JWT (Jupyter) cannot impersonate; only the impersonation-pattern
# clients (Superset, Voila/jupyter_svc, Open WebUI MCP) can. The Trino JWT
# user-mapping strips the Keycloak `service-account-` prefix, so identity.user
# matches the bare client_id here.
trino_service_principals := {"superset_svc", "jupyter_svc", "openwebui_mcp_svc"}

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
	"attribute_values": attribute_snapshot,
	"mask_phi_fields": object.get(user_attrs, "mask_phi_fields", ["unset"]),
	"row_filters": rowFilters,
}
