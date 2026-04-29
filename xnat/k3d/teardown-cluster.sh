#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME=${CLUSTER_NAME:-scout-xnat-dev}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

k3d cluster delete "$CLUSTER_NAME"
rm -f "$HERE/kubeconfig"
echo "Cluster $CLUSTER_NAME deleted. Certs under $HERE/certs/ left in place."
