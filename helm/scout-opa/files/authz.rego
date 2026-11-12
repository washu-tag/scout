# OPA's own API authorization (--authorization=basic). Only the decision
# paths Trino queries and the health probes are reachable; every read of
# raw data, ad-hoc query, and policy/data write is rejected.
package system.authz

default allow := false

trino_endpoints := {"allow", "rowFilters", "batchColumnMasks"}

allow if {
	input.method == "POST"
	count(input.path) == 4
	input.path[0] == "v1"
	input.path[1] == "data"
	input.path[2] == "trino"
	trino_endpoints[input.path[3]]
}

allow if {
	input.method == "GET"
	input.path == ["health"]
}
