#!/bin/sh
# Sends a test notice to the launchpad bell as the example app. For a dev site
# with launchpad notices on and this chart installed with notifications.enabled.
# Reads the app's client secret with kubectl, so it needs access to the app's
# namespace, plus curl and jq.
#
#   ./send-notice.sh <scout host> user <username>
#   ./send-notice.sh <scout host> role <role>      e.g. example-app-admin
#   ./send-notice.sh <scout host> withdraw
#
# NAMESPACE, CLIENT_ID and LAUNCHPAD_NAMESPACE default to scout-example,
# example-app and scout-core.
set -eu
host=$1
action=$2
ns=${NAMESPACE:-scout-example}
client=${CLIENT_ID:-example-app}
port=${LOCAL_PORT:-3399}

secret=$(kubectl -n "$ns" get secret "$client-keycloak-client" -o jsonpath='{.data.client-secret}' | base64 -d)
token=$(curl -sf "https://keycloak.$host/realms/scout/protocol/openid-connect/token" \
  -d grant_type=client_credentials -d client_id="$client" --data-urlencode client_secret="$secret" \
  -d scope=launchpad-notifications | jq -r .access_token)

# The public launchpad address sits behind the sign-in gate, so call its Service.
kubectl -n "${LAUNCHPAD_NAMESPACE:-scout-core}" port-forward svc/launchpad "$port:3000" >/dev/null &
forward=$!
trap 'kill $forward' EXIT
sleep 2
api="http://127.0.0.1:$port/api/notifications"

case $action in
  user) recipients="{\"users\": [\"$3\"]}" ;;
  role) recipients="{\"roles\": [\"$3\"]}" ;;
  withdraw)
    curl -s -X DELETE "$api?collapseKey=example-test" -H "Authorization: Bearer $token"
    echo
    exit
    ;;
  *) echo "usage: $0 <scout host> user <name> | role <role> | withdraw" >&2; exit 2 ;;
esac

curl -s -X POST "$api" -H "Authorization: Bearer $token" -H 'Content-Type: application/json' -d "{
  \"recipients\": $recipients,
  \"category\": \"example\",
  \"title\": \"Test notice from the example app\",
  \"detail\": \"Sent by send-notice.sh\",
  \"link\": \"/\",
  \"collapseKey\": \"example-test\"
}"
echo
