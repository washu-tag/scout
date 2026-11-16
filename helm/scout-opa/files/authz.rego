# OPA's own API authorization (--authorization=basic). The API has no
# authentication, so this allow-list is what stops a pod that can reach OPA
# from reading user data or rewriting policy. Must cover every opa.policy.*
# URI Trino is configured with.
package system.authz

default allow := false

trino_decisions := {"allow", "rowFilters", "batchColumnMasks"}

allow if {
	input.method == "POST"
	count(input.path) == 4
	input.path[0] == "v1"
	input.path[1] == "data"
	input.path[2] == "trino"
	trino_decisions[input.path[3]]
}

allow if {
	input.method == "POST"
	input.path == ["v1", "data", "scout", "cache_fingerprint"]
}

allow if {
	input.method == "GET"
	input.path == ["health"]
}
