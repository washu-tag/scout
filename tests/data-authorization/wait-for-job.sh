#!/usr/bin/env bash
# Wait for a Job to reach a terminal state (Complete or Failed),
# returning non-zero on Failed. `kubectl wait --for=condition=X`
# blocks on a single condition; the twin-wait pattern below races
# both so the script returns as soon as either resolves -- otherwise
# a Failed Job stalls the runner until --timeout fires.
#
# Usage: wait-for-job.sh <namespace> <job-name> [timeout-seconds]
# KUBECTL env var overrides the kubectl invocation (e.g. 'sudo -E kubectl').

set -euo pipefail

namespace=$1
job=$2
timeout=${3:-300}

kubectl_cmd=${KUBECTL:-kubectl}

$kubectl_cmd wait --for=condition=complete --timeout="${timeout}s" "job/${job}" -n "${namespace}" &
completion_pid=$!
$kubectl_cmd wait --for=condition=failed --timeout="${timeout}s" "job/${job}" -n "${namespace}" && exit 1 &
failure_pid=$!
wait -n "$completion_pid" "$failure_pid"
