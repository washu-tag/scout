# What Report Viewer keys cached results on: a hash of what decides a user's
# query results (their record in the user bundle and the deployed policy), and
# when that bundle was published, since OPA's replicas each poll it on their
# own and can disagree until every one has.
package scout

cache_fingerprint := {
	"hash": crypto.sha256(json.marshal({
		"user": object.get(data.users, input.user, null),
		"revision": data.policy_revision,
	})),
	"published_ms": users_published_ms,
}

# Keycloak stamps each bundle's manifest revision with its publish time (epoch ms).
default users_published_ms := null

users_published_ms := to_number(data.system.bundles["scout-users"].manifest.revision)
