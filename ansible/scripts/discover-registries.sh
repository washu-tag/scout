#!/usr/bin/env bash
# Script to discover all container registries used by Helm charts in Scout deployments
# Uses helm-images plugin to extract images from charts
# See: https://github.com/nikhilsbhat/helm-images

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLAYBOOKS_DIR="$(cd "${SCRIPT_DIR}/../playbooks" && pwd)"
HARBOR_DEFAULTS="${SCRIPT_DIR}/../roles/harbor/defaults/main.yaml"
SCOUT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "${TEMP_DIR}"' EXIT

echo "=== Scout Registry Discovery Tool ==="
echo ""

# Check if helm-images plugin is installed
if ! helm images version &>/dev/null; then
    echo "Error: helm-images plugin not found!"
    echo "Install it with: helm plugin install https://github.com/nikhilsbhat/helm-images.git"
    exit 1
fi

echo "Scanning playbooks for Helm charts..."
echo ""

# Find all helm chart references in playbooks
ALL_CHART_REFS=$(grep -r "chart_ref:" "${PLAYBOOKS_DIR}" --include="*.yaml" --include="*.yml" 2>/dev/null | \
    grep -v "^[[:space:]]*#" | \
    sed "s/.*chart_ref:[[:space:]]*//" | \
    sed "s/^['\"]*//" | \
    sed "s/['\"]$//" | \
    grep -v "^$" | \
    sort -u)

# Split into external charts (repo/chart) and local charts (with variables)
EXTERNAL_CHARTS=$(echo "${ALL_CHART_REFS}" | grep -v "{{" | grep "/" || true)
LOCAL_CHART_REFS=$(echo "${ALL_CHART_REFS}" | grep "{{" || true)

if [ -z "${EXTERNAL_CHARTS}" ] && [ -z "${LOCAL_CHART_REFS}" ]; then
    echo "No Helm charts found in playbooks"
    exit 1
fi

echo "Found external Helm charts:"
if [ -n "${EXTERNAL_CHARTS}" ]; then
    echo "${EXTERNAL_CHARTS}" | sed 's/^/  - /'
else
    echo "  (none)"
fi
echo ""

echo "Found local Helm charts:"
if [ -n "${LOCAL_CHART_REFS}" ]; then
    echo "${LOCAL_CHART_REFS}" | sed 's/^/  - /'
else
    echo "  (none)"
fi
echo ""

# Extract registries from each chart
ALL_IMAGES="${TEMP_DIR}/all_images.txt"
touch "${ALL_IMAGES}"

echo "Discovering Helm repos from playbooks..."
echo ""

# Find helm repo add commands in playbooks
HELM_REPOS=$(grep -r "helm_repository:" "${PLAYBOOKS_DIR}" --include="*.yaml" --include="*.yml" -A 2 2>/dev/null | \
    grep -E "(name:|repo_url:)" | \
    sed 's/^[^:]*://' | \
    sed 's/^[[:space:]]*//' | \
    sed 's/name:[[:space:]]*//' | \
    sed 's/repo_url:[[:space:]]*//' | \
    paste - - | \
    sed 's/[[:space:]]/|/' | \
    sort -u)

if [ -n "${HELM_REPOS}" ]; then
    # Get list of existing repos
    EXISTING_REPOS=$(helm repo list -o json 2>/dev/null | grep -o '"name":"[^"]*"' | cut -d'"' -f4 || true)

    echo "Adding Helm repos found in playbooks:"
    while IFS='|' read -r repo_name repo_url; do
        [ -z "${repo_name}" ] && continue
        echo "  - ${repo_name}: ${repo_url}"

        # Check if repo already exists
        if echo "${EXISTING_REPOS}" | grep -q "^${repo_name}$"; then
            echo "    → Already exists"
        else
            # Try to add the repo
            ADD_OUTPUT=$(helm repo add "${repo_name}" "${repo_url}" 2>&1)
            if echo "${ADD_OUTPUT}" | grep -q -E "(successfully|has been added)"; then
                echo "    ✓ Added successfully"
            else
                echo "    ✗ Failed to add"
                echo "${ADD_OUTPUT}" | sed 's/^/      /' | head -3
            fi
        fi
    done <<< "${HELM_REPOS}"

    echo ""
    echo "Updating Helm repo cache..."
    helm repo update 2>/dev/null || true
    echo ""
fi

echo "Extracting images from external charts..."
echo ""

if [ -n "${EXTERNAL_CHARTS}" ]; then
    while IFS= read -r chart; do
        [ -z "${chart}" ] && continue
        echo "Processing: ${chart}"

        # Parse chart reference (repo/chart)
        if [[ "${chart}" =~ ^([^/]+)/([^/]+)$ ]]; then
            REPO="${BASH_REMATCH[1]}"
            CHART_NAME="${BASH_REMATCH[2]}"
        else
            echo "  → Warning: Cannot parse chart reference"
            continue
        fi

        # Try to get images from the chart
        # Note: This requires the helm repo to be added
        ERROR_FILE="${TEMP_DIR}/helm_error_${CHART_NAME}.txt"
        if helm images get "${REPO}/${CHART_NAME}" 2>"${ERROR_FILE}" >> "${ALL_IMAGES}"; then
            echo "  ✓ Extracted images"
        else
            echo "  ✗ Failed to extract"
            # Show first line of error for debugging
            head -1 "${ERROR_FILE}" | sed 's/^/    /' || true
        fi
    done <<< "${EXTERNAL_CHARTS}"
fi

echo ""
echo "Processing local charts..."

# Find local helm charts (search in the actual helm directory, not the variables)
HELM_DIR="${SCOUT_ROOT}/helm"
if [ -d "${HELM_DIR}" ]; then
    LOCAL_CHARTS=$(find "${HELM_DIR}" -name "Chart.yaml" -type f 2>/dev/null | sed 's|/Chart.yaml||')

    for chart_path in ${LOCAL_CHARTS}; do
        chart_name=$(basename "${chart_path}")
        echo "Processing: ${chart_name} (local)"

        ERROR_FILE="${TEMP_DIR}/helm_error_local_${chart_name}.txt"
        if timeout 10 helm images get "${chart_path}" 2>"${ERROR_FILE}" >> "${ALL_IMAGES}"; then
            echo "  ✓ Extracted images"
        else
            echo "  ✗ Failed to extract"
            # Show first line of error for debugging
            head -1 "${ERROR_FILE}" | sed 's/^/    /' || true
        fi
    done
else
    echo "  → Helm directory not found at ${HELM_DIR}"
fi

echo ""
echo "=== Registry Analysis ==="
echo ""

# Extract registries from images
# Format: registry/repo/image:tag or just repo/image:tag (implies docker.io)
REGISTRIES_LIST=$(cat "${ALL_IMAGES}" | \
    grep -E "^[a-z0-9]" | \
    cut -d' ' -f1 | \
    sed 's|:.*||' | \
    awk -F/ '{
        if (NF >= 3 && $1 ~ /\./) {
            print $1
        } else if (NF >= 2 && $1 ~ /\./) {
            print $1
        } else {
            print "docker.io"
        }
    }' | \
    sort -u)

echo "Registries discovered:"
echo "${REGISTRIES_LIST}" | sed 's/^/  - /'

echo ""
echo "=== Coverage Check ==="
echo ""

# Read configured Harbor proxies from role defaults
if [ ! -f "${HARBOR_DEFAULTS}" ]; then
    echo "Warning: Harbor defaults file not found at ${HARBOR_DEFAULTS}"
    echo "Skipping coverage check."
    exit 0
fi

CONFIGURED_REGISTRIES=$(grep -A 100 "^harbor_registry_proxies:" "${HARBOR_DEFAULTS}" | \
    grep "public_registry:" | \
    sed 's/.*public_registry:[[:space:]]*//' | \
    sort -u)

echo "Currently configured in Harbor (from ${HARBOR_DEFAULTS}):"
echo "${CONFIGURED_REGISTRIES}" | sed 's/^/  - /'

echo ""
echo "Coverage analysis:"

MISSING=""
while IFS= read -r registry; do
    if echo "${CONFIGURED_REGISTRIES}" | grep -q "^${registry}$"; then
        echo "  ✓ ${registry} (covered)"
    else
        echo "  ✗ ${registry} (MISSING - needs Harbor proxy!)"
        MISSING="${MISSING}${registry}\n"
    fi
done <<< "${REGISTRIES_LIST}"

echo ""

if [ -z "${MISSING}" ]; then
    echo "✓ All registries are covered!"
else
    echo "⚠ Missing registries detected!"
    echo ""
    echo "Add these to ${HARBOR_DEFAULTS}:"
    echo ""
    echo -e "${MISSING}" | while IFS= read -r registry; do
        [ -z "${registry}" ] && continue
        reg_name=$(echo "${registry}" | sed 's/[.-]//g' | cut -d'/' -f1)
        echo "  - public_registry: ${registry}"
        echo "    registry_name: ${reg_name}"
        echo "    registry_type: docker-registry"
        echo "    registry_url: https://${registry}"
        echo "    project_name: ${reg_name}-proxy"
        echo ""
    done
fi

echo ""
echo "Full image list saved to: ${ALL_IMAGES}"
echo "Note: This script may miss some images if Helm repos are not configured locally."
echo "For complete coverage, also check actual deployed images on cluster nodes with:"
echo "  sudo /usr/local/bin/k3s crictl images | tail -n +2 | cut -d ' ' -f 1 | cut -d '/' -f 1 | sort -u"
