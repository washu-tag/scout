#!/bin/sh
# Forgejo job tokens cannot push images, so every user gets a package-only
# token as their REGISTRY_TOKEN Actions secret.
set -u
api="$FORGEJO_URL/api/v1"
name=scout-build

call() { curl -sf -u "$ADMIN_USER:$ADMIN_PASSWORD" -H 'Content-Type: application/json' "$@"; }

provision() {
  user=$1
  tokens=$(call "$api/users/$user/tokens?limit=50") || { echo "listing tokens for $user failed"; return; }
  echo "$tokens" | grep -q "\"name\":\"$name\"" && return
  token=$(call -X POST "$api/users/$user/tokens" -d "{\"name\":\"$name\",\"scopes\":[\"write:package\"]}" | sed -n 's/.*"sha1":"\([^"]*\)".*/\1/p')
  [ -n "$token" ] || { echo "creating token for $user failed"; return; }
  if call -X PUT "$api/user/actions/secrets/REGISTRY_TOKEN?sudo=$user" -d "{\"data\":\"$token\"}" >/dev/null; then
    echo "provisioned $user"
  else
    echo "saving secret for $user failed"
    call -X DELETE "$api/users/$user/tokens/$name" >/dev/null
  fi
}

while :; do
  page=1
  while users=$(call "$api/admin/users?limit=50&page=$page" | grep -o '"login":"[^"]*"' | cut -d'"' -f4) && [ -n "$users" ]; do
    for user in $users; do
      [ "$user" = "$ADMIN_USER" ] || provision "$user"
    done
    page=$((page + 1))
  done
  sleep "$INTERVAL_SECONDS"
done
