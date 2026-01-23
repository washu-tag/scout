#!/bin/bash
#
# scout-publish.sh - Publish a playbook to Scout
#
# Usage: ./scripts/scout-publish.sh <notebook-dir> \
#   --title "Title" --description "..." --icon "chart" --color "amber"
#
# This script:
# 1. Copies notebook files to the Voila dynamic playbooks PVC
# 2. Updates the playbook-registry ConfigMap for Launchpad
#
# Prerequisites:
# - kctl configured with cluster access
# - Voila and Launchpad deployed with dynamic playbooks enabled
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
    echo "Usage: $0 <notebook-dir> --title \"Title\" --description \"...\" --icon \"icon\" --color \"color\" [--kubeconfig PATH]"
    echo ""
    echo "Arguments:"
    echo "  notebook-dir    Directory containing the playbook (e.g., analytics/notebooks/my-playbook)"
    echo ""
    echo "Options:"
    echo "  --title         Display title for the playbook"
    echo "  --description   Brief description (1-2 sentences)"
    echo "  --icon          Icon name: users, chart, sparkles, clipboard, document, beaker"
    echo "  --color         Color name: violet, rose, cyan, emerald, amber, blue, indigo, pink"
    echo "  --kubeconfig    Path to kubeconfig file (default: \$KUBECONFIG or ~/.kube/config)"
    echo ""
    echo "Environment Variables:"
    echo "  KUBECONFIG          Path to kubeconfig file"
    echo "  VOILA_NAMESPACE     Voila namespace (default: scout-analytics)"
    echo "  LAUNCHPAD_NAMESPACE Launchpad namespace (default: scout-core)"
    echo ""
    echo "Example:"
    echo "  $0 analytics/notebooks/ct-utilization \\"
    echo "    --title \"CT Utilization\" \\"
    echo "    --description \"Scan volumes by modality over time\" \\"
    echo "    --icon \"chart\" \\"
    echo "    --color \"amber\" \\"
    echo "    --kubeconfig ~/.kube/scout/config"
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

NOTEBOOK_DIR="$1"
shift

TITLE=""
DESCRIPTION=""
ICON=""
COLOR=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --title)
            TITLE="$2"
            shift 2
            ;;
        --description)
            DESCRIPTION="$2"
            shift 2
            ;;
        --icon)
            ICON="$2"
            shift 2
            ;;
        --color)
            COLOR="$2"
            shift 2
            ;;
        --kubeconfig)
            SCOUT_KUBECONFIG="$2"
            shift 2
            ;;
        --help|-h)
            print_usage
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            print_usage
            exit 1
            ;;
    esac
done

# Validate required arguments
if [ -z "$TITLE" ] || [ -z "$DESCRIPTION" ] || [ -z "$ICON" ] || [ -z "$COLOR" ]; then
    log_error "Missing required arguments"
    print_usage
    exit 1
fi

# Validate notebook directory
if [ ! -d "$NOTEBOOK_DIR" ]; then
    log_error "Notebook directory not found: $NOTEBOOK_DIR"
    exit 1
fi

# Find notebook file
NOTEBOOK_FILE=$(find "$NOTEBOOK_DIR" -maxdepth 1 -name "*.ipynb" | head -n 1)
if [ -z "$NOTEBOOK_FILE" ]; then
    log_error "No .ipynb file found in $NOTEBOOK_DIR"
    exit 1
fi

PLAYBOOK_ID=$(basename "$NOTEBOOK_DIR")
NOTEBOOK_NAME=$(basename "$NOTEBOOK_FILE")

log_info "Publishing playbook: $PLAYBOOK_ID"
log_info "  Title: $TITLE"
log_info "  Description: $DESCRIPTION"
log_info "  Notebook: $NOTEBOOK_NAME"
log_info "  Icon: $ICON"
log_info "  Color: $COLOR"

# Step 1: Get Voila pod name
log_info "Finding Voila pod..."
VOILA_POD=$(kctl get pod -n "$VOILA_NAMESPACE" -l app.kubernetes.io/name=voila -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -z "$VOILA_POD" ]; then
    log_error "Could not find Voila pod in namespace $VOILA_NAMESPACE"
    log_info "Make sure Voila is deployed with dynamicPlaybooks.enabled=true"
    exit 1
fi

log_info "Found Voila pod: $VOILA_POD"

# Step 2: Copy notebook files to PVC
log_info "Copying notebook files to Voila..."
DEST_PATH="/home/jovyan/notebooks/dynamic/$PLAYBOOK_ID"

# Create destination directory
kctl exec -n "$VOILA_NAMESPACE" "$VOILA_POD" -- mkdir -p "$DEST_PATH"

# Copy all files from the notebook directory
for file in "$NOTEBOOK_DIR"/*; do
    if [ -f "$file" ]; then
        filename=$(basename "$file")
        log_info "  Copying $filename..."
        kctl cp "$file" "$VOILA_NAMESPACE/$VOILA_POD:$DEST_PATH/$filename"
    fi
done

log_success "Notebook files copied to Voila"

# Step 3: Update playbook registry ConfigMap
log_info "Updating playbook registry..."

# Get current ConfigMap data (if exists)
CURRENT_DATA=$(kctl get configmap "$CONFIGMAP_NAME" -n "$LAUNCHPAD_NAMESPACE" -o jsonpath='{.data.playbooks\.json}' 2>/dev/null || echo "[]")

# Parse current playbooks and remove existing entry with same ID
NEW_PLAYBOOK=$(cat <<EOF
{
  "id": "dynamic/$PLAYBOOK_ID",
  "title": "$TITLE",
  "description": "$DESCRIPTION",
  "notebook": "$NOTEBOOK_NAME",
  "icon": "$ICON",
  "color": "$COLOR"
}
EOF
)

# Use jq to merge the playbook into the registry (or create new if empty)
if command -v jq &> /dev/null; then
    # Remove any existing entry with the same ID and add new one
    UPDATED_DATA=$(echo "$CURRENT_DATA" | jq --argjson new "$NEW_PLAYBOOK" '
        [.[] | select(.id != $new.id)] + [$new]
    ')
else
    # Fallback: simple append if jq not available
    log_warn "jq not found, using simple append (may create duplicates)"
    if [ "$CURRENT_DATA" = "[]" ]; then
        UPDATED_DATA="[$NEW_PLAYBOOK]"
    else
        # Remove trailing ] and add new entry
        UPDATED_DATA="${CURRENT_DATA%]}, $NEW_PLAYBOOK]"
    fi
fi

# Create or update the ConfigMap
kctl create configmap "$CONFIGMAP_NAME" \
    -n "$LAUNCHPAD_NAMESPACE" \
    --from-literal="playbooks.json=$UPDATED_DATA" \
    --dry-run=client -o yaml | kctl apply -f -

log_success "Playbook registry updated"

# Step 4: Get the playbook URL
SERVER_HOSTNAME=$(kctl get ingress -n "$VOILA_NAMESPACE" -l app.kubernetes.io/name=voila -o jsonpath='{.items[0].spec.rules[0].host}' 2>/dev/null || echo "playbooks.your-domain.com")
PLAYBOOK_URL="https://$SERVER_HOSTNAME/voila/render/dynamic/$PLAYBOOK_ID/$NOTEBOOK_NAME"

echo ""
log_success "Playbook published successfully!"
echo ""
echo -e "${GREEN}Playbook URL:${NC}"
echo "  $PLAYBOOK_URL"
echo ""
echo -e "${BLUE}Note:${NC} The playbook will appear on the Launchpad within a minute."
echo "      Refresh the page to see it."
