package authz_test

import data.system.authz
import rego.v1

allowed(method, path) if authz.allow with input as {"method": method, "path": path}

test_trino_decisions_allowed if {
	allowed("POST", ["v1", "data", "trino", "allow"])
	allowed("POST", ["v1", "data", "trino", "rowFilters"])
	allowed("POST", ["v1", "data", "trino", "batchColumnMasks"])
}

test_cache_fingerprint_allowed if {
	allowed("POST", ["v1", "data", "scout", "cache_fingerprint"])
}

test_health_allowed if {
	allowed("GET", ["health"])
}

test_raw_data_reads_denied if {
	not allowed("GET", ["v1", "data", "users"])
	not allowed("POST", ["v1", "data", "users"])
	not allowed("GET", ["v1", "data", "users", "alice"])
	not allowed("POST", ["v1", "data"])
	not allowed("GET", ["v1", "data"])
}

test_whole_packages_denied if {
	not allowed("POST", ["v1", "data", "trino"])
	not allowed("POST", ["v1", "data", "scout"])
	not allowed("POST", ["v1", "data", "trino", "allow", "extra"])
}

test_other_rules_in_allowed_packages_denied if {
	not allowed("POST", ["v1", "data", "trino", "user_attrs"])
	not allowed("POST", ["v1", "data", "scout", "other"])
}

test_wrong_method_denied if {
	not allowed("GET", ["v1", "data", "trino", "allow"])
	not allowed("GET", ["v1", "data", "scout", "cache_fingerprint"])
	not allowed("POST", ["health"])
}

test_query_and_compile_denied if {
	not allowed("POST", ["v1", "query"])
	not allowed("GET", ["v1", "query"])
	not allowed("POST", ["v1", "compile"])
}

test_policy_and_data_writes_denied if {
	not allowed("GET", ["v1", "policies"])
	not allowed("PUT", ["v1", "policies", "evil"])
	not allowed("DELETE", ["v1", "policies", "policy.rego"])
	not allowed("PUT", ["v1", "data", "users"])
	not allowed("PATCH", ["v1", "data"])
	not allowed("DELETE", ["v1", "data", "users"])
}

test_metrics_denied if {
	not allowed("GET", ["metrics"])
}
