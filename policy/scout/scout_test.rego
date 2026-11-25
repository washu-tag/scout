package scout_test

import data.scout
import rego.v1

users := {
	"alice": {"enabled": true, "allowed_facilities": ["A"]},
	"bob": {"enabled": true, "allowed_facilities": ["B"]},
}

bundles := {"scout-users": {"manifest": {"revision": "1795000000000"}}}

fingerprint(user, users_data, revision) := fp if {
	fp := scout.cache_fingerprint.hash with input as {"user": user}
		with data.users as users_data
		with data.policy_revision as revision
		with data.system.bundles as bundles
}

test_is_a_sha256_hex_digest if {
	regex.match(`^[0-9a-f]{64}$`, fingerprint("alice", users, "r1"))
}

test_stable_for_unchanged_inputs if {
	fingerprint("alice", users, "r1") == fingerprint("alice", users, "r1")
}

test_differs_between_users if {
	fingerprint("alice", users, "r1") != fingerprint("bob", users, "r1")
}

test_changes_with_the_users_record if {
	changed := object.union(users, {"alice": {"enabled": true, "allowed_facilities": ["A", "B"]}})
	fingerprint("alice", users, "r1") != fingerprint("alice", changed, "r1")
}

test_ignores_other_users_records if {
	changed := object.union(users, {"bob": {"enabled": false}})
	fingerprint("alice", users, "r1") == fingerprint("alice", changed, "r1")
}

test_changes_with_policy_revision if {
	fingerprint("alice", users, "r1") != fingerprint("alice", users, "r2")
}

test_unknown_user_still_has_a_fingerprint if {
	fingerprint("carol", users, "r1") != fingerprint("alice", users, "r1")
}

test_undefined_without_policy_revision if {
	not scout.cache_fingerprint with input as {"user": "alice"}
		with data.users as users
		with data.system.bundles as bundles
}

test_reports_when_the_user_bundle_was_published if {
	fp := scout.cache_fingerprint with input as {"user": "alice"}
		with data.users as users
		with data.policy_revision as "r1"
		with data.system.bundles as bundles
	fp.published_ms == 1795000000000
}

test_publish_time_null_without_the_bundle if {
	fp := scout.cache_fingerprint with input as {"user": "alice"}
		with data.users as users
		with data.policy_revision as "r1"
		with data.system.bundles as {}
	fp.published_ms == null
}

test_hash_ignores_the_publish_time if {
	later := {"scout-users": {"manifest": {"revision": "1795000099999"}}}
	a := scout.cache_fingerprint.hash with input as {"user": "alice"}
		with data.users as users
		with data.policy_revision as "r1"
		with data.system.bundles as bundles
	b := scout.cache_fingerprint.hash with input as {"user": "alice"}
		with data.users as users
		with data.policy_revision as "r1"
		with data.system.bundles as later
	a == b
}
