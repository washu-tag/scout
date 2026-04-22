#!/usr/bin/env bash
# Add the TERMS_AND_CONDITIONS required action to every user in $REALM who
# doesn't already have it pending and hasn't already accepted.
#
# Usage: bash backfill_terms.sh <realm-name>
# Assumes kcadm is already authenticated (config credentials has been called).

set -euo pipefail

REALM="${1:?realm name required}"
KCADM=/opt/keycloak/bin/kcadm.sh

added=0
skipped=0

for uid in $("$KCADM" get users -r "$REALM" --fields id --format csv --noquotes -q max=10000); do
    user_json=$("$KCADM" get "users/$uid" -r "$REALM")
    if echo "$user_json" | grep -q '"terms_and_conditions"'; then
        skipped=$((skipped + 1))
        continue
    fi
    if echo "$user_json" | grep -q '"TERMS_AND_CONDITIONS"'; then
        skipped=$((skipped + 1))
        continue
    fi
    "$KCADM" update "users/$uid" -r "$REALM" \
        -s 'requiredActions+=["TERMS_AND_CONDITIONS"]'
    added=$((added + 1))
    echo "added TERMS_AND_CONDITIONS to $uid"
done

echo "backfill complete: added=$added skipped=$skipped"
