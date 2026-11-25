#!/bin/sh
# Creates or updates the shared build workflow in Forgejo from this chart's copy.
set -eu
api="$FORGEJO_URL/api/v1"
path=.forgejo/workflows/build.yml

call() { curl -sf -u "$ADMIN_USER:$ADMIN_PASSWORD" -H 'Content-Type: application/json' "$@"; }

until call "$api/version" >/dev/null; do echo "waiting for forgejo"; sleep 5; done

call "$api/orgs/$ORG" >/dev/null ||
  call -X POST "$api/orgs" -d "{\"username\":\"$ORG\",\"visibility\":\"public\"}" >/dev/null
call "$api/repos/$ORG/$REPO" >/dev/null ||
  call -X POST "$api/orgs/$ORG/repos" -d "{\"name\":\"$REPO\",\"private\":false,\"auto_init\":true,\"default_branch\":\"main\"}" >/dev/null

content=$(base64 -w0 < /workflow/build.yml)
current=$(call "$api/repos/$ORG/$REPO/contents/$path" || true)
sha=$(echo "$current" | sed -n 's/.*"sha":"\([^"]*\)".*/\1/p')
existing=$(echo "$current" | sed -n 's/.*"content":"\([^"]*\)".*/\1/p')

if [ -z "$sha" ]; then
  call -X POST "$api/repos/$ORG/$REPO/contents/$path" -d "{\"message\":\"add shared build workflow\",\"content\":\"$content\"}" >/dev/null
  echo "created $ORG/$REPO/$path"
elif [ "$existing" != "$content" ]; then
  call -X PUT "$api/repos/$ORG/$REPO/contents/$path" -d "{\"message\":\"update shared build workflow\",\"content\":\"$content\",\"sha\":\"$sha\"}" >/dev/null
  echo "updated $ORG/$REPO/$path"
else
  echo "$ORG/$REPO/$path is current"
fi
