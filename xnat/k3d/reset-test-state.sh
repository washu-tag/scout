#!/usr/bin/env bash
#
# Reset just enough state to re-run xnat/k3d/test-login.sh from a clean
# slate, without tearing down the whole cluster.
#
# What we wipe:
#   - Active Keycloak SSO sessions for alice and bob (so the next agent-
#     browser login isn't auto-completed by a leftover cookie).
#   - All XNAT users with the `keycloak-*` username pattern (so the
#     openid-auth-plugin's forceUserCreate runs again on the next login,
#     re-exercising the user-provisioning path end-to-end).
#   - agent-browser sessions named login-alice / login-bob (so Chromium
#     starts each test with a fresh profile).
#
# What we keep:
#   - Keycloak realm, client, group, role, flow override.
#   - alice and bob themselves in Keycloak.
#   - The XNAT helm release and its data.
#
# Use:
#   ./xnat/k3d/reset-test-state.sh
#   ./xnat/k3d/test-login.sh        # rerun the assertions
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export KUBECONFIG="${KUBECONFIG:-${SCRIPT_DIR}/kubeconfig}"

KC_POD=$(kubectl -n scout-core get pod -l app=keycloak \
           -o jsonpath='{.items[0].metadata.name}')
KC="kubectl -n scout-core exec -i ${KC_POD} -- /opt/keycloak/bin/kcadm.sh"

${KC} config credentials --server http://localhost:8080 \
  --realm master --user admin --password admin >/dev/null

# Logout active KC sessions for alice and bob. POST /users/{id}/logout is
# idempotent — if no sessions are active, the call is a no-op.
echo "Logging out alice/bob KC sessions..."
for user in alice bob; do
  uid=$(${KC} get users -r scout -q "username=${user}" \
          --fields id --format csv --noquotes 2>/dev/null | tail -1)
  if [[ -n "${uid}" ]]; then
    ${KC} create "users/${uid}/logout" -r scout >/dev/null 2>&1 || true
  fi
done

# Delete `keycloak-*` users from XNAT directly in Postgres. The XNAT REST
# endpoints only support disabling users (PUT enabled=false → 200) — there's
# no DELETE — but a disabled user blocks the openid plugin's user-create
# path on next login (xdatUser.isEnabled() check fails before forceUserCreate
# would run), so disable-only doesn't fully reset the test. SQL deletion is
# the only path that lets the next login go through user provisioning again.
echo "Deleting keycloak-* users from XNAT (via psql)..."
kubectl -n xnat exec xnat-postgres-1 -c postgres -- \
  psql -U postgres -d xnat -c "
    BEGIN;
    DELETE FROM xhbm_xdat_user_auth WHERE xdat_username LIKE 'keycloak-%';
    DELETE FROM xdat_user           WHERE login         LIKE 'keycloak-%';
    COMMIT;
  " 2>&1 | tail -5

# Close agent-browser sessions for the test users.
echo "Closing agent-browser sessions..."
for user in alice bob; do
  agent-browser --session "login-${user}" close --all 2>/dev/null || true
done

echo
echo "Reset complete. Run ${SCRIPT_DIR}/test-login.sh to re-test."
