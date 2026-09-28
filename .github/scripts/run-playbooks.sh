#!/usr/bin/env bash
# Space-separated stages run in order; '+'-joined playbooks in a stage run
# concurrently. Run from ansible/: run-playbooks.sh "postgres auth+lake trino"
set -uo pipefail
ANSIBLE_PLAYBOOK=${ANSIBLE_PLAYBOOK:-sudo -E /opt/pipx_bin/ansible-playbook}

for stage in $1; do
  echo "=== Stage: $stage ==="
  pids=()
  names=()
  for playbook in ${stage//+/ }; do
    echo "=== Deploying $playbook ==="
    ($ANSIBLE_PLAYBOOK -i inventory.yaml --diff "playbooks/${playbook}.yaml" 2>&1 |
      sed -u "s/^/[$playbook] /"
    exit "${PIPESTATUS[0]}") &
    pids+=($!)
    names+=("$playbook")
  done
  failed=0
  for i in "${!pids[@]}"; do
    if ! wait "${pids[$i]}"; then
      echo "::error::playbook ${names[$i]} failed"
      failed=1
    fi
  done
  [ "$failed" = 0 ] || exit 1
  echo "=== Stage done: $stage ==="
done
