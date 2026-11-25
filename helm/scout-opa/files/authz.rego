# OPA's own API authorization (--authorization=basic). The API has no
# authentication, so this allow-list is what stops a pod that can reach OPA
# from reading user data or rewriting policy. Must cover every opa.policy.*
# URI Trino is configured with.
package system.authz

default allow := false

trino_decisions := {"allow", "rowFilters", "batchColumnMasks"}

allow if {
	input.method == "POST"
	no_params
	count(input.path) == 4
	input.path[0] == "v1"
	input.path[1] == "data"
	input.path[2] == "trino"
	trino_decisions[input.path[3]]
}

allow if {
	input.method == "POST"
	no_params
	input.path == ["v1", "data", "scout", "cache_fingerprint"]
}

allow if {
	input.method == "GET"
	input.path == ["health"]
}

# Query parameters such as ?explain=full or ?instrument would return evaluation
# traces that include the user's bundle record. Neither caller sends any.
no_params if object.get(input, "params", {}) == {}
