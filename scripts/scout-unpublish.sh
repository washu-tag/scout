#!/bin/bash
#
# scout-unpublish.sh - Remove a published playbook from Scout
#
# Usage: ./scripts/scout-unpublish.sh <playbook-id> [--kubeconfig PATH]
#
# This script:
# 1. Removes notebook files from the Voila dynamic playbooks PVC
# 2. Removes the entry from the playbook-registry ConfigMap
#

set -e

# Default namespaces (can be overridden via environment)
VOILA_NAMESPACE="${VOILA_NAMESPACE:-scout-analytics}"
LAUNCHPAD_NAMESPACE="${LAUNCHPAD_NAMESPACE:-scout-core}"
CONFIGMAP_NAME="${CONFIGMAP_NAME:-launchpad-playbooks}"

# Default kubeconfig - can be overridden via --kubeconfig or KUBECONFIG env var
SCOUT_KUBECONFIG="${KUBECONFIG:-/Users/katealpert/Scout/scout-demo/.kube/scout/tagdev-control-04/config}"

# kubectl wrapper that uses the configured kubeconfig
kctl() {
    kubectl --kubeconfig="$SCOUT_KUBECONFIG" "$@"
}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_usage() {
    echo "Usage: $0 <playbook-id> [--kubeconfig PATH]"
    echo ""
    echo "Arguments:"
    echo "  playbook-id    ID of the playbook to unpublish (e.g., quality-metrics)"
    echo ""
    echo "Options:"
    echo "  --kubeconfig    Path to kubeconfig file (default: \$KUBECONFIG or ~/.kube/config)"
    echo ""
    echo "Environment Variables:"
    echo "  KUBECONFIG          Path to kubeconfig file"
    echo "  VOILA_NAMESPACE     Voila namespace (default: scout-analytics)"
    echo "  LAUNCHPAD_NAMESPACE Launchpad namespace (default: scout-core)"
    echo ""
    echo "Example:"
    echo "  $0 quality-metrics --kubeconfig ~/.kube/scout/config"
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Parse arguments
if [ $# -lt 1 ]; then
    print_usage
    exit 1
fi

PLAYBOOK_ID=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --kubeconfig)
            SCOUT_KUBECONFIG="$2"
            shift 2
            ;;
        --help|-h)
            print_usage
            exit 0
            ;;
        -*)
            log_error "Unknown option: $1"
            print_usage
            exit 1
            ;;
        *)
            PLAYBOOK_ID="$1"
            shift
            ;;
    esac
done

if [ -z "$PLAYBOOK_ID" ]; then
    log_error "Missing playbook-id argument"
    print_usage
    exit 1
fi

log_info "Unpublishing playbook: $PLAYBOOK_ID"

# Step 1: Get Voila pod name
log_info "Finding Voila pod..."
VOILA_POD=$(kctl get pod -n "$VOILA_NAMESPACE" -l app.kubernetes.io/name=voila -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -z "$VOILA_POD" ]; then
    log_warn "Could not find Voila pod in namespace $VOILA_NAMESPACE"
    log_info "Skipping file removal, continuing with registry cleanup..."
else
    log_info "Found Voila pod: $VOILA_POD"

    # Step 2: Remove notebook files from PVC
    log_info "Removing notebook files from Voila..."
    DEST_PATH="/home/jovyan/notebooks/dynamic/$PLAYBOOK_ID"

    # Check if directory exists and remove it
    if kctl exec -n "$VOILA_NAMESPACE" "$VOILA_POD" -- test -d "$DEST_PATH" 2>/dev/null; then
        kctl exec -n "$VOILA_NAMESPACE" "$VOILA_POD" -- rm -rf "$DEST_PATH"
        log_success "Notebook files removed from Voila"
    else
        log_warn "Playbook directory not found in Voila PVC (may not have been published there)"
    fi
fi

# Step 3: Update playbook registry ConfigMap
log_info "Updating playbook registry..."

# Get current ConfigMap data (if exists)
CURRENT_DATA=$(kctl get configmap "$CONFIGMAP_NAME" -n "$LAUNCHPAD_NAMESPACE" -o jsonpath='{.data.playbooks\.json}' 2>/dev/null || echo "[]")

if [ "$CURRENT_DATA" == "[]" ]; then
    log_warn "Playbook registry is already empty"
else
    # Use jq to remove the playbook entry
    if command -v jq &> /dev/null; then
        UPDATED_DATA=$(echo "$CURRENT_DATA" | jq --arg id "dynamic/$PLAYBOOK_ID" '[.[] | select(.id != $id)]')

        # Check if anything was actually removed
        BEFORE_COUNT=$(echo "$CURRENT_DATA" | jq 'length')
        AFTER_COUNT=$(echo "$UPDATED_DATA" | jq 'length')

        if [ "$BEFORE_COUNT" == "$AFTER_COUNT" ]; then
            log_warn "Playbook 'dynamic/$PLAYBOOK_ID' not found in registry"
        else
            # Update the ConfigMap
            kctl create configmap "$CONFIGMAP_NAME" \
                -n "$LAUNCHPAD_NAMESPACE" \
                --from-literal="playbooks.json=$UPDATED_DATA" \
                --dry-run=client -o yaml | kctl apply -f -
            log_success "Playbook removed from registry"
        fi
    else
        log_error "jq is required to update the registry. Please install jq."
        exit 1
    fi
fi

echo ""
log_success "Playbook '$PLAYBOOK_ID' unpublished!"
echo ""
echo -e "${BLUE}Note:${NC} Refresh the Launchpad page to see the changes."
