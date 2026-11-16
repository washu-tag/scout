# Hash of what decides a user's query results: their record in the user
# bundle and the deployed policy. Report Viewer keys cached results on it.
package scout

cache_fingerprint := crypto.sha256(json.marshal({
	"user": object.get(data.users, input.user, null),
	"revision": data.policy_revision,
}))
