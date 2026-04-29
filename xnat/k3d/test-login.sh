#!/usr/bin/env bash
#
# OIDC login assertions for XNAT on local k3d.
#
# - alice (in scout-user) → expect to land on the XNAT homepage authenticated
# - bob   (not in scout-user) → expect to be rejected at the OIDC callback
#
# Pass AB_INSECURE=1 to skip TLS verification in agent-browser (use when you
# skipped `mkcert -install` or are using the openssl fallback cert).

set -euo pipefail

XNAT_URL="https://xnat.localtest.me"
KC_URL="https://keycloak.localtest.me"

AB_OPTS=()
if [[ "${AB_INSECURE:-0}" == "1" ]]; then
  AB_OPTS+=(--ignore-https-errors)
fi

run_case() {
  local user="$1" pw="$2" expect="$3"   # expect = "allow" | "deny"
  local session="login-${user}"

  agent-browser --session "$session" ${AB_OPTS[@]+"${AB_OPTS[@]}"} close --all || true

  # Navigate straight to the openid-login route. The plugin is loaded and
  # routes work, but the homepage isn't rendering the "Sign in with Scout"
  # link (XNAT isn't adding the openid plugin to its linked_providers
  # collection — separate UX issue, doesn't affect the auth flow).
  agent-browser --session "$session" ${AB_OPTS[@]+"${AB_OPTS[@]}"} open "$XNAT_URL/openid-login?providerId=keycloak"
  agent-browser --session "$session" ${AB_OPTS[@]+"${AB_OPTS[@]}"} wait 'input[name="username"]'
  agent-browser --session "$session" ${AB_OPTS[@]+"${AB_OPTS[@]}"} fill 'input[name="username"]' "$user"
  agent-browser --session "$session" ${AB_OPTS[@]+"${AB_OPTS[@]}"} fill 'input[name="password"]' "$pw"
  agent-browser --session "$session" ${AB_OPTS[@]+"${AB_OPTS[@]}"} find role button click --name "Sign In"

  # Give XNAT time to redirect back and finish the OIDC callback.
  agent-browser --session "$session" ${AB_OPTS[@]+"${AB_OPTS[@]}"} wait 4000

  local url body
  url=$(agent-browser --session "$session" ${AB_OPTS[@]+"${AB_OPTS[@]}"} get url)
  body=$(agent-browser --session "$session" ${AB_OPTS[@]+"${AB_OPTS[@]}"} get text body)

  echo "[$user] landed on: $url"

  if [[ "$expect" == "allow" ]]; then
    [[ "$url" == "$XNAT_URL"/* ]] \
      || { echo "FAIL [$user]: expected to land on XNAT, got $url"; return 1; }
    grep -qiE 'disabled|locked|not enabled|access denied' <<<"$body" \
      && { echo "FAIL [$user]: saw rejection text on what should be a successful login"; return 1; }
    grep -qiE 'sign in with scout|please log in' <<<"$body" \
      && { echo "FAIL [$user]: still on the login page after OIDC round-trip"; return 1; }
    echo "PASS [$user]: logged in as scout-user member"
  else
    if [[ "$url" == "$XNAT_URL"/* ]] \
       && ! grep -qiE 'disabled|locked|not enabled|access denied|sign in' <<<"$body"; then
      echo "FAIL [$user]: non-member appears to be logged in (url=$url)"; return 1
    fi
    echo "PASS [$user]: non-member was rejected"
  fi
}

run_case alice alice-pw allow
run_case bob   bob-pw   deny

echo
echo "All login assertions passed."
