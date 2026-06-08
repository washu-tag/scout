package trino_test

import data.trino
import rego.v1

# Run with `opa test policy/`.
#
# Test strategy: the policy fetches user attributes from Keycloak via
# http.send. Mocking the network round-trips per test is noisy, so each
# test overrides `trino.user_attrs` directly with `with` — this is the
# Rego-supported way to replace a rule's value during evaluation. The
# integration test against a deployed OPA exercises the http.send path
# end-to-end.

# === Fixtures =================================================================

fixture_filtered_tables := [{"catalog": "delta", "schema": "default", "table": "reports"}]

fixture_attribute_filters := {"allowed_facilities": {"column": "sending_facility"}}

# Two-attribute fixture for tests exercising the AND composition (e.g.
# facility + modality), proving that adding a dimension is a data
# update rather than a policy edit.
fixture_two_attribute_filters := {
	"allowed_facilities": {"column": "sending_facility"},
	"allowed_modalities": {"column": "modality"},
}

# View-only fixture for testing direct-access denial.
fixture_hidden_tables := [{"catalog": "delta", "schema": "default", "table": "reports_report_patient_mapping"}]

# View-owner principals: identities whose underlying-table reads bypass
# row filters and column masks (since they're materializing a DEFINER
# view on behalf of an invoker whose AuthZ applies at the view level).
fixture_view_owner_principals := ["trino"]

fixture_masked_columns := ["patient_name", "full_patient_name", "zip_or_postal_code"]

# Standard `with data.*` clauses every test injects. Concatenated inline below.

# === Input builders ===========================================================

select_input(user, groups, catalog, schema, table) := {
	"context": {"identity": {"user": user, "groups": groups}},
	"action": {
		"operation": "SelectFromColumns",
		"resource": {"table": {"catalogName": catalog, "schemaName": schema, "tableName": table}},
	},
}

batch_mask_input(user, groups, catalog, schema, table, column_names) := {
	"context": {"identity": {"user": user, "groups": groups}},
	"action": {
		"operation": "GetColumnMask",
		"filterResources": [{"column": {
			"catalogName": catalog,
			"schemaName": schema,
			"tableName": table,
			"columnName": col,
			"columnType": "varchar",
		}} |
		col := column_names[_]
		],
	},
}

# Variant of batch_mask_input that lets each column carry its own SQL
# type. Used by the type-dispatch mask tests where varchar columns
# get '[REDACTED]' and everything else gets bare NULL.
batch_mask_input_typed(user, groups, catalog, schema, table, columns) := {
	"context": {"identity": {"user": user, "groups": groups}},
	"action": {
		"operation": "GetColumnMask",
		"filterResources": [{"column": {
			"catalogName": catalog,
			"schemaName": schema,
			"tableName": table,
			"columnName": c.name,
			"columnType": c.type,
		}} |
		c := columns[_]
		],
	},
}

execute_query_input(user, groups) := {
	"context": {"identity": {"user": user, "groups": groups}},
	"action": {"operation": "ExecuteQuery"},
}

# === Allow =================================================================

test_unauthenticated_denied if {
	inp := {
		"context": {"identity": {"user": "", "groups": []}},
		"action": {"operation": "SelectFromColumns", "resource": {"table": {"catalogName": "delta", "schemaName": "default", "tableName": "reports"}}},
	}
	not trino.allow with input as inp
}

test_missing_identity_denied if {
	inp := {"action": {"operation": "SelectFromColumns", "resource": {"table": {"catalogName": "delta", "schemaName": "default", "tableName": "reports"}}}}
	not trino.allow with input as inp
}

# The two tests above deny because the default user_attrs ({"enabled": false})
# fails user_enabled — NOT because of the identity_present gate. To actually
# exercise identity_present, force user_attrs to enabled+approved so
# user_enabled passes; an empty identity.user must then still be denied. Without
# these, dropping `identity_present` from the allow rules goes uncaught.

test_empty_identity_denied_even_when_attrs_enabled if {
	inp := select_input("", ["scout-user"], "delta", "default", "reports")
	not trino.allow with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
}

test_empty_identity_denied_on_catalogless_op if {
	# Same isolation for the catalog-less allow rule (ExecuteQuery).
	inp := execute_query_input("", ["scout-user"])
	not trino.allow with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
}

test_user_select_on_delta_allowed if {
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports")
	trino.allow with input as inp with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
}

test_admin_select_on_delta_allowed if {
	inp := select_input("admin", ["scout-admin"], "delta", "default", "reports")
	trino.allow with input as inp with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
}

test_information_schema_allowed if {
	inp := select_input("alice", ["scout-user"], "delta", "information_schema", "tables")
	trino.allow with input as inp with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
}

test_system_catalog_allowed if {
	inp := select_input("alice", ["scout-user"], "system", "runtime", "queries")
	trino.allow with input as inp with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
}

test_execute_query_no_resource_allowed if {
	inp := execute_query_input("alice", ["scout-user"])
	trino.allow with input as inp with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
}

test_access_catalog_delta_allowed if {
	inp := {
		"context": {"identity": {"user": "alice", "groups": ["scout-user"]}},
		"action": {"operation": "AccessCatalog", "resource": {"catalog": {"name": "delta"}}},
	}
	trino.allow with input as inp with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
}

test_filter_catalogs_delta_allowed if {
	# Trino calls FilterCatalogs once per catalog (or batched) when answering
	# SHOW CATALOGS. Resource shape is `catalog.name`, not table.catalogName,
	# which is what tripped up the import job before the rule refactor.
	inp := {
		"context": {"identity": {"user": "alice", "groups": ["scout-user"]}},
		"action": {"operation": "FilterCatalogs", "resource": {"catalog": {"name": "delta"}}},
	}
	trino.allow with input as inp with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
}

test_filter_catalogs_system_allowed if {
	inp := {
		"context": {"identity": {"user": "alice", "groups": ["scout-user"]}},
		"action": {"operation": "FilterCatalogs", "resource": {"catalog": {"name": "system"}}},
	}
	trino.allow with input as inp with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
}

test_filter_catalogs_unknown_denied if {
	inp := {
		"context": {"identity": {"user": "alice", "groups": ["scout-user"]}},
		"action": {"operation": "FilterCatalogs", "resource": {"catalog": {"name": "iceberg"}}},
	}
	not trino.allow with input as inp with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
}

# Catalog-allowlist deny coverage for the schema- and table-level rules.
# FilterCatalogs (catalog-level rule) already had a negative case, but the
# separate `in allowed_catalogs` checks on the schema-level (ShowTables/
# FilterSchemas) and table-level (SelectFromColumns/...) rules had no
# unknown-catalog deny test. An unguarded catalog (iceberg) must be denied.

test_show_tables_unknown_catalog_denied if {
	inp := {
		"context": {"identity": {"user": "alice", "groups": ["scout-user"]}},
		"action": {"operation": "ShowTables", "resource": {"schema": {"catalogName": "iceberg", "schemaName": "default"}}},
	}
	not trino.allow with input as inp with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
}

test_select_unknown_catalog_denied if {
	inp := select_input("alice", ["scout-user"], "iceberg", "default", "reports")
	not trino.allow with input as inp with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
}

test_show_catalogs_no_resource_allowed if {
	inp := {
		"context": {"identity": {"user": "alice", "groups": ["scout-user"]}},
		"action": {"operation": "ShowCatalogs"},
	}
	trino.allow with input as inp with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
}

test_show_tables_schema_allowed if {
	inp := {
		"context": {"identity": {"user": "alice", "groups": ["scout-user"]}},
		"action": {"operation": "ShowTables", "resource": {"schema": {"catalogName": "delta", "schemaName": "default"}}},
	}
	trino.allow with input as inp with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
}

test_filter_schemas_allowed if {
	inp := {
		"context": {"identity": {"user": "alice", "groups": ["scout-user"]}},
		"action": {"operation": "FilterSchemas", "resource": {"schema": {"catalogName": "delta", "schemaName": "default"}}},
	}
	trino.allow with input as inp with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
}

# === View-only tables =========================================================
# Tables on data.hidden_tables are denied for direct access — both
# SELECT and SHOW TABLES — so a user can only reach the data via views
# defined over them (with SECURITY DEFINER, so the view's reads aren't
# re-checked against the user's permissions).

test_select_from_hidden_table_denied if {
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports_report_patient_mapping")
	not trino.allow with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.hidden_tables as fixture_hidden_tables
}

test_filter_tables_hides_hidden_table if {
	# FilterTables is what populates SHOW TABLES. Denying it for a
	# view-only table drops the table from the listing.
	inp := {
		"context": {"identity": {"user": "alice", "groups": ["scout-user"]}},
		"action": {
			"operation": "FilterTables",
			"resource": {"table": {"catalogName": "delta", "schemaName": "default", "tableName": "reports_report_patient_mapping"}},
		},
	}
	not trino.allow with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.hidden_tables as fixture_hidden_tables
}

test_show_columns_on_hidden_table_denied if {
	inp := {
		"context": {"identity": {"user": "alice", "groups": ["scout-user"]}},
		"action": {
			"operation": "ShowColumns",
			"resource": {"table": {"catalogName": "delta", "schemaName": "default", "tableName": "reports_report_patient_mapping"}},
		},
	}
	not trino.allow with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.hidden_tables as fixture_hidden_tables
}

test_baseline_hidden_table_blocked_without_inventory if {
	# Baseline (patient mapping) is hardcoded in policy/trino/main.rego;
	# direct SELECT must be denied even when data.hidden_tables is empty
	# (a partner deployment that hasn't added any site-specific entries).
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports_report_patient_mapping")
	not trino.allow with input as inp
		with trino.user_attrs as {"enabled": true, "groups": ["scout-user"]}
		with data.hidden_tables as []
}

test_inventory_hidden_table_blocked if {
	# Inventory-added hidden tables are unioned on top of the baseline.
	# A site-specific custom join table not in the baseline still gets
	# blocked when listed in data.hidden_tables.
	inp := select_input("alice", ["scout-user"], "delta", "default", "site_custom_join")
	not trino.allow with input as inp
		with trino.user_attrs as {"enabled": true, "groups": ["scout-user"]}
		with data.hidden_tables as [{"catalog": "delta", "schema": "default", "table": "site_custom_join"}]
}

# === bypass_hidden_tables ==================================================
# Per-user escape hatch from the view-only block. Setting Keycloak
# attribute bypass_hidden_tables: ["true"] permits direct SELECT
# on tables listed in data.hidden_tables. Default (attribute unset
# or "false") preserves the deny-everyone behavior tested above.

test_select_hidden_table_allowed_with_bypass if {
	# Admin user with bypass_hidden_tables: ["true"] can SELECT from
	# reports_report_patient_mapping directly.
	inp := select_input("admin", ["scout-admin"], "delta", "default", "reports_report_patient_mapping")
	trino.allow with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "bypass_hidden_tables": ["true"]}
		with data.hidden_tables as fixture_hidden_tables
}

test_filter_tables_shows_hidden_table_with_bypass if {
	# SHOW TABLES surfaces the view-only table for bypass-enabled users.
	inp := {
		"context": {"identity": {"user": "admin", "groups": ["scout-admin"]}},
		"action": {
			"operation": "FilterTables",
			"resource": {"table": {"catalogName": "delta", "schemaName": "default", "tableName": "reports_report_patient_mapping"}},
		},
	}
	trino.allow with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "bypass_hidden_tables": ["true"]}
		with data.hidden_tables as fixture_hidden_tables
}

test_bypass_false_does_not_unlock if {
	# Explicit "false" — same as unset — still blocks. Defense against
	# accidental string-typo bypass (e.g. ["yes"] or [""]).
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports_report_patient_mapping")
	not trino.allow with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "bypass_hidden_tables": ["false"]}
		with data.hidden_tables as fixture_hidden_tables
}

test_bypass_unset_does_not_unlock if {
	# Default deny remains when the user just doesn't have the attribute.
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports_report_patient_mapping")
	not trino.allow with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.hidden_tables as fixture_hidden_tables
}

test_bypass_non_true_value_does_not_unlock if {
	# Only the literal "true" unlocks. test_bypass_false_does_not_unlock's
	# comment claims a typo'd ["yes"] is defended against, but only ["false"]
	# was actually exercised — so loosening val[0] == "true" to != "false"
	# went uncaught. Pin it: ["yes"] must NOT bypass the hidden-table block.
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports_report_patient_mapping")
	not trino.allow with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "bypass_hidden_tables": ["yes"]}
		with data.hidden_tables as fixture_hidden_tables
}

test_bypass_only_unlocks_hidden_tables if {
	# Bypass doesn't affect anything else — row filters, masks, and the
	# enabled gate still apply. Disabled user with bypass is still denied.
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports_report_patient_mapping")
	not trino.allow with input as inp
		with trino.user_attrs as{"enabled": false, "bypass_hidden_tables": ["true"]}
		with data.hidden_tables as fixture_hidden_tables
}

test_non_hidden_table_still_allowed if {
	# Regression: adding a hidden_tables entry doesn't accidentally
	# affect tables outside the list.
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports")
	trino.allow with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.hidden_tables as fixture_hidden_tables
}

# === Prefix matching ==========================================================
# Table-list entries support {catalog, schema, table_prefix} in addition
# to the exact {catalog, schema, table} shape. Prefix matching covers a
# family of derivative tables (e.g. reports_*) without enumerating each.

test_hidden_prefix_match_denies_direct_select if {
	# A `secrets_` prefix entry in hidden_tables denies any
	# `secrets_*` table, not just one named "secrets_".
	inp := select_input("alice", ["scout-user"], "delta", "default", "secrets_pii")
	not trino.allow with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.hidden_tables as [{"catalog": "delta", "schema": "default", "table_prefix": "secrets_"}]
}

test_hidden_prefix_match_does_not_overreach_to_other_schema if {
	# A prefix entry is scoped to its catalog/schema — same prefix in
	# a different schema should NOT be matched.
	inp := select_input("alice", ["scout-user"], "delta", "audit", "secrets_pii")
	trino.allow with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.hidden_tables as [{"catalog": "delta", "schema": "default", "table_prefix": "secrets_"}]
}

test_row_filter_emitted_via_prefix_match if {
	# A prefix entry in filtered_tables emits row filters for every
	# matching table the same way an exact entry would.
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports_curated")
	expected := {{"expression": "sending_facility IN ('WUSM')"}}
	trino.rowFilters == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["WUSM"]}
		with data.attribute_filters as fixture_attribute_filters
		with data.filtered_tables as [{"catalog": "delta", "schema": "default", "table_prefix": "reports_"}]
}

test_row_filter_not_emitted_for_hidden_table_even_if_prefix_matches if {
	# The hidden_tables exclusion in attribute_scopes_table makes
	# sure a `reports_` prefix doesn't accidentally emit a filter
	# referencing a column that hidden mapping tables don't have.
	# Without the exclusion the filter would be emitted (then never
	# fire in practice due to the layered carve-outs), but it'd be a
	# latent footgun. Test the explicit guarantee.
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports_report_patient_mapping")
	count(trino.rowFilters) == 0 with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["WUSM"]}
		with data.attribute_filters as fixture_attribute_filters
		with data.filtered_tables as [{"catalog": "delta", "schema": "default", "table_prefix": "reports_"}]
		with data.hidden_tables as [{"catalog": "delta", "schema": "default", "table": "reports_report_patient_mapping"}]
}

test_row_filter_not_emitted_for_non_prefixed_table if {
	# Tables outside the prefix get no row filter, just like with
	# explicit entries.
	inp := select_input("alice", ["scout-user"], "delta", "default", "audit_log")
	count(trino.rowFilters) == 0 with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["WUSM"]}
		with data.attribute_filters as fixture_attribute_filters
		with data.filtered_tables as [{"catalog": "delta", "schema": "default", "table_prefix": "reports_"}]
}

test_create_view_with_select_bypasses_hidden_table_block if {
	# Trino calls CreateViewWithSelectFromColumns when validating a view's
	# underlying reads; identity is the view OWNER. This must succeed even
	# for tables in hidden_tables — otherwise SELECTing from any
	# DEFINER view over those tables fails.
	inp := {
		"context": {"identity": {"user": "trino", "groups": []}},
		"action": {
			"operation": "CreateViewWithSelectFromColumns",
			"resource": {"table": {"catalogName": "delta", "schemaName": "default", "tableName": "reports_report_patient_mapping"}},
		},
	}
	trino.allow with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.hidden_tables as fixture_hidden_tables
}

test_select_from_hidden_table_still_denied_for_invoker if {
	# Companion to the rule above: while the view's underlying read of a
	# view-only table is permitted (via CreateViewWithSelectFromColumns),
	# a direct SelectFromColumns by the invoker is still blocked.
	inp := {
		"context": {"identity": {"user": "alice", "groups": ["scout-user"]}},
		"action": {
			"operation": "SelectFromColumns",
			"resource": {"table": {"catalogName": "delta", "schemaName": "default", "tableName": "reports_report_patient_mapping"}},
		},
	}
	not trino.allow with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.hidden_tables as fixture_hidden_tables
}

# The CreateViewWithSelectFromColumns rule gates on user_enabled, but the
# bypass test above uses owner `trino` (enabled via injected attrs, and it
# serves as the positive case), so it can't catch a *dropped* enabled gate.
# This negative pins it: a non-system, disabled invoker must be denied.
test_create_view_denied_for_disabled_invoker if {
	inp := {
		"context": {"identity": {"user": "alice", "groups": ["scout-user"]}},
		"action": {
			"operation": "CreateViewWithSelectFromColumns",
			"resource": {"table": {"catalogName": "delta", "schemaName": "default", "tableName": "reports"}},
		},
	}
	not trino.allow with input as inp
		with trino.user_attrs as{"enabled": false, "groups": ["scout-user"]}
}

# === Row filters: facility-driven ============================================

test_no_attrs_emits_zero_row_clamp if {
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports")
	expected := {{"expression": "1=0"}}
	trino.rowFilters == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.attribute_filters as fixture_attribute_filters
		with data.filtered_tables as fixture_filtered_tables
}

test_single_facility_emits_in_clause if {
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports")
	expected := {{"expression": "sending_facility IN ('WUSM')"}}
	trino.rowFilters == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["WUSM"]}
		with data.attribute_filters as fixture_attribute_filters
		with data.filtered_tables as fixture_filtered_tables
}

test_multi_facility_emits_in_clause if {
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports")
	expected := {{"expression": "sending_facility IN ('WUSM','BJH')"}}
	trino.rowFilters == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["WUSM", "BJH"]}
		with data.attribute_filters as fixture_attribute_filters
		with data.filtered_tables as fixture_filtered_tables
}

test_wildcard_facility_emits_no_filter if {
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports")
	count(trino.rowFilters) == 0 with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["*"]}
		with data.attribute_filters as fixture_attribute_filters
		with data.filtered_tables as fixture_filtered_tables
}

test_wildcard_mixed_with_codes_emits_no_filter if {
	# `*` short-circuits before the regex pass — any other value is ignored
	# in the presence of the wildcard.
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports")
	count(trino.rowFilters) == 0 with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["WUSM", "*"]}
		with data.attribute_filters as fixture_attribute_filters
		with data.filtered_tables as fixture_filtered_tables
}

# Admins are treated identically to users — no group bypass. An admin who
# wants unrestricted access sets allowed_facilities: ["*"].
test_admin_without_wildcard_gets_filter if {
	inp := select_input("admin", ["scout-admin"], "delta", "default", "reports")
	expected := {{"expression": "sending_facility IN ('WUSM')"}}
	trino.rowFilters == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["WUSM"]}
		with data.attribute_filters as fixture_attribute_filters
		with data.filtered_tables as fixture_filtered_tables
}

test_admin_without_attrs_gets_zero_row_clamp if {
	inp := select_input("admin", ["scout-admin"], "delta", "default", "reports")
	expected := {{"expression": "1=0"}}
	trino.rowFilters == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.attribute_filters as fixture_attribute_filters
		with data.filtered_tables as fixture_filtered_tables
}

# Injection defense: bad values are dropped, not interpolated.

test_injection_attempt_dropped if {
	inp := select_input("mallory", ["scout-user"], "delta", "default", "reports")
	expected := {{"expression": "sending_facility IN ('WUSM')"}}
	trino.rowFilters == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["WUSM", "'); DROP TABLE reports; --"]}
		with data.attribute_filters as fixture_attribute_filters
		with data.filtered_tables as fixture_filtered_tables
}

test_only_invalid_values_emits_zero_row_clamp if {
	inp := select_input("mallory", ["scout-user"], "delta", "default", "reports")
	expected := {{"expression": "1=0"}}
	trino.rowFilters == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["bad with spaces", "$%^"]}
		with data.attribute_filters as fixture_attribute_filters
		with data.filtered_tables as fixture_filtered_tables
}

# Autocomplete safety: filters must not apply to information_schema or other
# tables outside the allowlist.

test_no_filter_on_information_schema if {
	inp := select_input("alice", ["scout-user"], "delta", "information_schema", "tables")
	count(trino.rowFilters) == 0 with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["WUSM"]}
		with data.attribute_filters as fixture_attribute_filters
		with data.filtered_tables as fixture_filtered_tables
}

test_no_filter_on_unscoped_delta_table if {
	# A delta.default table not in the attribute_filters allowlist gets no filter
	# — even when the user has attributes that would otherwise filter.
	inp := select_input("alice", ["scout-user"], "delta", "default", "other_table")
	count(trino.rowFilters) == 0 with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["WUSM"]}
		with data.attribute_filters as fixture_attribute_filters
		with data.filtered_tables as fixture_filtered_tables
}

test_no_filter_on_system_catalog if {
	inp := select_input("alice", ["scout-user"], "system", "runtime", "queries")
	count(trino.rowFilters) == 0 with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["WUSM"]}
		with data.attribute_filters as fixture_attribute_filters
		with data.filtered_tables as fixture_filtered_tables
}

# === Row filters: multi-attribute (generic mechanism) ========================
# These tests cover what the refactor enables — adding a new restriction
# dimension (modality) is a data-only change. The rego doesn't know about
# modality specifically; it loops over data.attribute_filters.

test_two_attributes_emit_two_filters if {
	# With both facility and modality configured, a user constrained on
	# both dimensions gets two AND-composed row filters.
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports")
	expected := {
		{"expression": "sending_facility IN ('WUSM')"},
		{"expression": "modality IN ('CT')"},
	}
	trino.rowFilters == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["WUSM"], "allowed_modalities": ["CT"]}
		with data.attribute_filters as fixture_two_attribute_filters
		with data.filtered_tables as fixture_filtered_tables
}

test_wildcard_one_attribute_still_filters_other if {
	# allowed_facilities=["*"] disables only the facility filter; the
	# modality filter still applies.
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports")
	expected := {{"expression": "modality IN ('CT','MR')"}}
	trino.rowFilters == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["*"], "allowed_modalities": ["CT", "MR"]}
		with data.attribute_filters as fixture_two_attribute_filters
		with data.filtered_tables as fixture_filtered_tables
}

test_missing_attribute_clamps_to_zero_rows if {
	# User has facility values but never set allowed_modalities. The
	# modality dimension has no wildcard and no valid values, so the
	# 1=0 clamp fires — defense-in-depth against forgetting to set
	# attributes on a new restriction dimension.
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports")
	expected := {
		{"expression": "sending_facility IN ('WUSM')"},
		{"expression": "1=0"},
	}
	trino.rowFilters == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["WUSM"]}
		with data.attribute_filters as fixture_two_attribute_filters
		with data.filtered_tables as fixture_filtered_tables
}

# View-owner principals bypass row filters and column masks because
# Trino re-evaluates the view's underlying reads against the view's
# OWNER identity for SECURITY DEFINER views. The invoker's AuthZ is
# still enforced at the view level (the view is itself in filtered_tables).

test_view_owner_bypasses_row_filter if {
	# 'trino' is the view-owner service principal; its underlying-table
	# reads must NOT be clamped, even though `trino` has no Keycloak
	# attributes (which would normally trigger the 1=0 clamp).
	inp := select_input("trino", [], "delta", "default", "reports")
	count(trino.rowFilters) == 0 with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.attribute_filters as fixture_attribute_filters
		with data.filtered_tables as fixture_filtered_tables
		with data.view_owner_principals as fixture_view_owner_principals
}

test_view_owner_bypasses_column_mask if {
	# Same exemption for column masks: a DEFINER view's underlying read
	# of a PHI column must surface the raw value so the invoker's mask
	# (applied at the view-level read) can substitute the redaction.
	inp := batch_mask_input("trino", [], "delta", "default", "reports",
		["patient_name"])
	count(trino.batchColumnMasks) == 0 with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.masked_columns as fixture_masked_columns
		with data.view_owner_principals as fixture_view_owner_principals
}

test_non_view_owner_still_gets_row_filter if {
	# Regression: declaring a view-owner principal doesn't accidentally
	# exempt other users (the invoker) from row filters.
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports")
	expected := {{"expression": "1=0"}}
	trino.rowFilters == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.attribute_filters as fixture_attribute_filters
		with data.filtered_tables as fixture_filtered_tables
		with data.view_owner_principals as fixture_view_owner_principals
}

# Per-attribute `tables` override: when an attribute filter declares its
# own tables list, that subset wins over the global filtered_tables.
# Used when a column only exists on a subset of the filtered tables.

test_attribute_tables_override_scopes_filter if {
	# Filter applies to the override's table, not the global list.
	override_filters := {"allowed_facilities": {
		"column": "sending_facility",
		"tables": [{"catalog": "delta", "schema": "default", "table": "reports"}],
	}}
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports")
	expected := {{"expression": "sending_facility IN ('WUSM')"}}
	trino.rowFilters == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["WUSM"]}
		with data.attribute_filters as override_filters
		with data.filtered_tables as [{"catalog": "delta", "schema": "default", "table": "other"}]
}

test_attribute_tables_override_skips_unscoped_table if {
	# Other tables in the global filtered_tables list don't pick up a
	# filter from an attribute that explicitly scopes to a subset.
	override_filters := {"allowed_facilities": {
		"column": "sending_facility",
		"tables": [{"catalog": "delta", "schema": "default", "table": "reports"}],
	}}
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports_dx")
	count(trino.rowFilters) == 0 with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["WUSM"]}
		with data.attribute_filters as override_filters
		with data.filtered_tables as [{"catalog": "delta", "schema": "default", "table": "reports_dx"}]
}

# === Column masks ============================================================

test_phi_columns_masked_when_default_unset if {
	# mask_phi_fields unset -> default mask
	inp := batch_mask_input("alice", ["scout-user"], "delta", "default", "reports",
		["patient_name", "modality", "sending_facility"])
	expected := {{"index": 0, "viewExpression": {"expression": "'[REDACTED]'"}}}
	trino.batchColumnMasks == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.masked_columns as fixture_masked_columns
}

test_phi_columns_masked_when_explicit_true if {
	inp := batch_mask_input("alice", ["scout-user"], "delta", "default", "reports",
		["patient_name"])
	expected := {{"index": 0, "viewExpression": {"expression": "'[REDACTED]'"}}}
	trino.batchColumnMasks == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "mask_phi_fields": ["true"]}
		with data.masked_columns as fixture_masked_columns
}

test_phi_columns_unmasked_when_explicit_false if {
	inp := batch_mask_input("alice", ["scout-user"], "delta", "default", "reports",
		["patient_name", "full_patient_name", "zip_or_postal_code"])
	count(trino.batchColumnMasks) == 0 with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "mask_phi_fields": ["false"]}
		with data.masked_columns as fixture_masked_columns
}

test_phi_columns_masked_when_empty_list if {
	# mask_phi_fields present but [] (e.g. an admin-API PUT that bypasses the
	# user-profile options validation) must still mask — only an explicit
	# ["false"] disables masking.
	inp := batch_mask_input("alice", ["scout-user"], "delta", "default", "reports",
		["patient_name"])
	expected := {{"index": 0, "viewExpression": {"expression": "'[REDACTED]'"}}}
	trino.batchColumnMasks == expected with input as inp
		with trino.user_attrs as {"enabled": true, "groups": ["scout-user"], "mask_phi_fields": []}
		with data.masked_columns as fixture_masked_columns
}

test_phi_columns_masked_when_value_not_false if {
	# Any value other than "false" masks (fail-safe for a malformed attribute).
	inp := batch_mask_input("alice", ["scout-user"], "delta", "default", "reports",
		["patient_name"])
	expected := {{"index": 0, "viewExpression": {"expression": "'[REDACTED]'"}}}
	trino.batchColumnMasks == expected with input as inp
		with trino.user_attrs as {"enabled": true, "groups": ["scout-user"], "mask_phi_fields": ["", "false"]}
		with data.masked_columns as fixture_masked_columns
}

test_admin_phi_unmasked_when_explicit_false if {
	# Admins use the same opt-in as users — set mask_phi_fields: "false"
	inp := batch_mask_input("admin", ["scout-admin"], "delta", "default", "reports",
		["patient_name"])
	count(trino.batchColumnMasks) == 0 with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"], "mask_phi_fields": ["false"]}
		with data.masked_columns as fixture_masked_columns
}

test_admin_phi_masked_when_default if {
	# Admin without explicit mask_phi_fields=false still gets masks applied
	inp := batch_mask_input("admin", ["scout-admin"], "delta", "default", "reports",
		["patient_name"])
	expected := {{"index": 0, "viewExpression": {"expression": "'[REDACTED]'"}}}
	trino.batchColumnMasks == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.masked_columns as fixture_masked_columns
}

test_multiple_phi_columns_in_batch_all_masked if {
	inp := batch_mask_input("alice", ["scout-user"], "delta", "default", "reports",
		["patient_name", "modality", "zip_or_postal_code"])
	expected := {
		{"index": 0, "viewExpression": {"expression": "'[REDACTED]'"}},
		{"index": 2, "viewExpression": {"expression": "'[REDACTED]'"}},
	}
	trino.batchColumnMasks == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.masked_columns as fixture_masked_columns
}

test_no_masked_columns_in_batch_emits_empty if {
	inp := batch_mask_input("alice", ["scout-user"], "delta", "default", "reports",
		["modality", "sending_facility"])
	count(trino.batchColumnMasks) == 0 with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.masked_columns as fixture_masked_columns
}

test_mask_only_in_delta_catalog if {
	# A PHI-named column outside delta gets no mask.
	inp := batch_mask_input("alice", ["scout-user"], "system", "runtime", "queries",
		["patient_name"])
	count(trino.batchColumnMasks) == 0 with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.masked_columns as fixture_masked_columns
}

test_mask_applies_to_any_delta_table if {
	# Mask is column-scoped within delta — same column on a different delta
	# table is also masked.
	inp := batch_mask_input("alice", ["scout-user"], "delta", "default", "reports_latest",
		["patient_name"])
	expected := {{"index": 0, "viewExpression": {"expression": "'[REDACTED]'"}}}
	trino.batchColumnMasks == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.masked_columns as fixture_masked_columns
}

# Mask expression depends on column type: varchar columns get the
# literal '[REDACTED]', everything else gets bare NULL so Trino's
# analyzer can coerce it to the column's declared type.

test_complex_type_masked_with_null if {
	# full_patient_name is array(row(...)) in Scout's schema. The mask
	# is bare NULL — relies on Trino's analyzer to type-coerce.
	complex_type := "array(row(family_name varchar, given_name varchar))"
	inp := batch_mask_input_typed("alice", ["scout-user"], "delta", "default", "reports",
		[{"name": "full_patient_name", "type": complex_type}])
	expected := {{"index": 0, "viewExpression": {"expression": "NULL"}}}
	trino.batchColumnMasks == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.masked_columns as fixture_masked_columns
}

test_varchar_with_length_masked_with_redacted if {
	# varchar(255) still gets the visible '[REDACTED]' mask — the
	# startswith("varchar") check covers parameterized varchars too.
	inp := batch_mask_input_typed("alice", ["scout-user"], "delta", "default", "reports",
		[{"name": "patient_name", "type": "varchar(255)"}])
	expected := {{"index": 0, "viewExpression": {"expression": "'[REDACTED]'"}}}
	trino.batchColumnMasks == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.masked_columns as fixture_masked_columns
}

test_mixed_types_in_batch if {
	# A batch with one varchar and one complex column emits the right
	# mask for each.
	inp := batch_mask_input_typed("alice", ["scout-user"], "delta", "default", "reports", [
		{"name": "patient_name", "type": "varchar"},
		{"name": "full_patient_name", "type": "array(row(family_name varchar))"},
	])
	expected := {
		{"index": 0, "viewExpression": {"expression": "'[REDACTED]'"}},
		{"index": 1, "viewExpression": {"expression": "NULL"}},
	}
	trino.batchColumnMasks == expected with input as inp
		with trino.user_attrs as{"enabled": true, "groups": ["scout-user"]}
		with data.masked_columns as fixture_masked_columns
}

# === ImpersonateUser ==========================================================
# Only the three configured Trino service principals may impersonate. End users
# (scout-user, scout-admin) cannot — they connect with their own JWT and that's
# the identity Trino checks.

impersonate_input(principal, target_user) := {
	"context": {"identity": {"user": principal, "groups": []}},
	"action": {
		"operation": "ImpersonateUser",
		"resource": {"user": {"user": target_user}},
	},
}

test_superset_svc_can_impersonate if {
	trino.allow with input as impersonate_input("superset_svc", "alice@example.org")
}

test_openwebui_mcp_svc_can_impersonate if {
	trino.allow with input as impersonate_input("openwebui_mcp_svc", "alice@example.org")
}

test_voila_svc_can_impersonate if {
	trino.allow with input as impersonate_input("voila_svc", "alice@example.org")
}

test_random_user_cannot_impersonate if {
	not trino.allow with input as impersonate_input("alice@example.org", "bob@example.org")
}

test_admin_cannot_impersonate if {
	# Even scout-admin members can't impersonate from a regular user identity —
	# only the dedicated service principals are on the allowlist.
	not trino.allow with input as impersonate_input("admin@example.org", "bob@example.org")
}

# === Bundle-driven user attributes (data.users) ==============================
# ADR 0021: per-user attributes come from OPA's data document (populated
# by the Keycloak SPI listener via a MinIO-hosted bundle), not from
# http.send. Tests below verify the end-to-end /allow gate honors the
# bundle's `enabled` flag and treats absent-from-bundle as deny.

test_user_attrs_reads_from_data_users if {
	# The end-to-end rule: identity → data.users[user] → attrs map.
	# Tests for downstream rules use `with trino.user_attrs as{...}`
	# directly; this one nails down that the lookup path itself works.
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports")
	expected := {"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["WUSM"]}
	trino.user_attrs == expected with input as inp
		with data.users as {"alice": expected, "bob": {"enabled": false}}
}

test_user_attrs_defaults_to_disabled_when_absent_from_bundle if {
	# A user not in the bundle gets the default {"enabled": false}.
	# This is the "newly onboarded but not yet provisioned" case, and
	# also the "listener hasn't published a bundle yet" case during
	# the very first deploy.
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports")
	trino.user_attrs == {"enabled": false} with input as inp
		with data.users as {"bob": {"enabled": true, "groups": ["scout-user"]}}
}

test_user_attrs_default_when_data_users_missing_entirely if {
	# Pre-bundle-load (OPA pod just started, bundle plugin hasn't
	# finished its first pull): data.users key may not exist yet.
	# Default must still resolve cleanly.
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports")
	trino.user_attrs == {"enabled": false} with input as inp
}

test_enabled_user_in_bundle_can_select if {
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports")
	trino.allow with input as inp
		with data.users as {"alice": {"enabled": true, "groups": ["scout-user"], "allowed_facilities": ["WUSM"]}}
}

test_enabled_user_not_in_approved_group_denied if {
	# Federated user that landed in Keycloak via the upstream IdP but
	# hasn't gone through Scout's approval flow yet (not in scout-user).
	# Bundle reflects enabled=true but groups=[] — the approval gate
	# should still deny.
	inp := select_input("federated", ["scout-user"], "delta", "default", "reports")
	not trino.allow with input as inp
		with data.users as {"federated": {"enabled": true, "groups": [], "allowed_facilities": ["WUSM"]}}
}

test_enabled_user_in_scout_admin_can_select if {
	# scout-admin is in the hardcoded approved_groups set in main.rego;
	# a user in scout-admin but not scout-user is still approved.
	inp := select_input("admin", ["scout-admin"], "delta", "default", "reports")
	trino.allow with input as inp
		with data.users as {"admin": {"enabled": true, "groups": ["scout-admin"], "allowed_facilities": ["*"]}}
}

test_disabled_user_in_bundle_denied_at_allow if {
	# An admin disabling alice in Keycloak → listener publishes
	# enabled:false → next decision after bundle pull denies at /allow,
	# not just at row-filter level. Defense in depth over the 1=0
	# zero-row clamp.
	inp := select_input("alice", ["scout-user"], "delta", "default", "reports")
	not trino.allow with input as inp
		with data.users as {"alice": {"enabled": false, "allowed_facilities": ["WUSM"]}}
}

test_unknown_user_denied_at_allow if {
	# Identity is set, but the user isn't in data.users. Could be a
	# typo'd JWT, a service principal not on the impersonation
	# allowlist, or a user provisioned in Keycloak but not yet
	# published to the bundle. Deny.
	inp := select_input("ghost", ["scout-user"], "delta", "default", "reports")
	not trino.allow with input as inp with data.users as {}
}

test_disabled_user_denied_on_show_catalogs if {
	# Catalog-less ops also gate on user_enabled — a disabled user
	# can't even enumerate catalogs.
	inp := {
		"context": {"identity": {"user": "alice", "groups": ["scout-user"]}},
		"action": {"operation": "ShowCatalogs"},
	}
	not trino.allow with input as inp
		with data.users as {"alice": {"enabled": false}}
}

# === System-identity carve-outs ==============================================
# View-owner principals (trino) and impersonation service principals
# (superset_svc, openwebui_mcp_svc) are not in data.users. The
# is_system_identity carve-out exempts them from user_enabled so their
# operations don't deny by default.

test_view_owner_allowed_without_bundle_entry if {
	# `trino` is the view owner for DEFINER views created by
	# hl7-transformer. When an invoker queries one of those views,
	# Trino evaluates the underlying-table reads as `trino`. That
	# identity isn't in Keycloak's user list, so it won't be in the
	# bundle — but it must still be allowed.
	inp := select_input("trino", [], "delta", "default", "reports")
	trino.allow with input as inp
		with data.users as {}
		with data.view_owner_principals as fixture_view_owner_principals
}

test_impersonation_service_principal_allowed_without_bundle_entry if {
	# superset_svc only ever calls ImpersonateUser; the impersonated
	# end-user is what subsequent decisions evaluate. The service
	# principal itself isn't in the bundle.
	not trino.user_attrs.enabled with input as impersonate_input("superset_svc", "alice@example.org")
		with data.users as {}
	trino.allow with input as impersonate_input("superset_svc", "alice@example.org")
		with data.users as {}
}
