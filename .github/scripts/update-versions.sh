#!/usr/bin/env bash
#
# Update version files for Scout releases.
#
# Usage:
#   ./update-versions.sh 2.1.0   # Set release version
#   ./update-versions.sh dev     # Reset to dev versions
#
set -euo pipefail

VERSION="${1:?Usage: $0 <version|dev>}"

# Dev version values
DEV_DOCKER_TAG="latest"
DEV_HELM_VERSION="0.0.0-dev"
DEV_PYTHON_VERSION="0.0.dev0"

# Determine mode and set target versions
if [[ "$VERSION" == "dev" ]]; then
    DOCKER_TAG="$DEV_DOCKER_TAG"
    HELM_VERSION="$DEV_HELM_VERSION"
    PYTHON_VERSION="$DEV_PYTHON_VERSION"
    echo "Resetting to dev versions..."
else
    # Validate release version format
    if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "Error: Invalid version format. Expected X.Y.Z (e.g., 2.1.0) or 'dev'"
        exit 1
    fi
    DOCKER_TAG="$VERSION"
    HELM_VERSION="$VERSION"
    PYTHON_VERSION="$VERSION"
    echo "Updating version files to $VERSION..."
fi

# Change to repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# Helper function to update a file and verify the change
update_file() {
    local file="$1"
    local pattern="$2"
    local replacement="$3"
    local description="$4"

    if [[ ! -f "$file" ]]; then
        echo "  ERROR: File not found: $file"
        exit 1
    fi

    # BSD sed (macOS) requires a backup extension with -i; GNU sed (Linux) doesn't.
    # Using -i.bak creates a backup we can compare against to verify the change.
    sed -i.bak -E "s|$pattern|$replacement|" "$file"

    # Fail if sed didn't change anything (pattern may not have matched)
    if cmp -s "$file" "${file}.bak"; then
        echo "  ERROR: No changes made to $file for $description"
        echo "         Pattern may not have matched: $pattern"
        rm -f "${file}.bak"
        exit 1
    fi

    rm -f "${file}.bak"
    echo "  - $description: $file"
}

echo ""
echo "Ansible role defaults (Docker image tags)..."
update_file "ansible/roles/scout_common/defaults/main.yaml" \
    "^(jupyter_singleuser_image_tag:) .+$" \
    "\\1 $DOCKER_TAG" \
    "jupyter_singleuser_image_tag"

update_file "ansible/roles/extractor/defaults/main.yaml" \
    "^(hl7log_extractor_image_tag:) .+$" \
    "\\1 $DOCKER_TAG" \
    "hl7log_extractor_image_tag"

update_file "ansible/roles/extractor/defaults/main.yaml" \
    "^(hl7_transformer_image_tag:) .+$" \
    "\\1 $DOCKER_TAG" \
    "hl7_transformer_image_tag"

update_file "ansible/roles/launchpad/defaults/main.yaml" \
    "^(launchpad_image_tag:) .+$" \
    "\\1 $DOCKER_TAG" \
    "launchpad_image_tag"

echo ""
echo "Python package..."
update_file "extractor/hl7-transformer/pyproject.toml" \
    '^(version = ")[^"]+(")'  \
    "\\1$PYTHON_VERSION\\2" \
    "pyproject.toml version"

echo "$DOCKER_TAG" > "extractor/hl7-transformer/VERSION"
echo "  - VERSION file: extractor/hl7-transformer/VERSION"

echo ""
echo "Gradle build files..."
update_file "extractor/hl7log-extractor/build.gradle" \
    "^(version = ')[^']+(')" \
    "\\1$DOCKER_TAG\\2" \
    "hl7log-extractor version"

update_file "keycloak/event-listener/build.gradle" \
    "^(version = ')[^']+(')" \
    "\\1$DOCKER_TAG\\2" \
    "keycloak event-listener version"

update_file "tests/ingest/build.gradle" \
    "^(version = ')[^']+(')" \
    "\\1$DOCKER_TAG\\2" \
    "tests version"

echo ""
echo "npm packages..."
update_file "launchpad/package.json" \
    '("version": ")[^"]+(")'  \
    "\\1$DOCKER_TAG\\2" \
    "package.json version"

update_file "tests/auth/package.json" \
    '("version": ")[^"]+(")'  \
    "\\1$DOCKER_TAG\\2" \
    "auth tests package.json version"

echo ""
echo "Helm charts (Scout applications - version + appVersion)..."
for chart in helm/launchpad/Chart.yaml \
             helm/extractor/hl7-transformer/Chart.yaml \
             helm/extractor/hl7log-extractor/Chart.yaml; do
    update_file "$chart" \
        "^(version:) .+$" \
        "\\1 $HELM_VERSION" \
        "chart version"
    update_file "$chart" \
        '^(appVersion: ")[^"]+(")'  \
        "\\1$DOCKER_TAG\\2" \
        "chart appVersion"
done

echo ""
echo "Helm charts (external applications - version only)..."
update_file "helm/hive-metastore/Chart.yaml" \
    "^(version:) .+$" \
    "\\1 $HELM_VERSION" \
    "hive-metastore chart version"

update_file "helm/voila/Chart.yaml" \
    "^(version:) .+$" \
    "\\1 $HELM_VERSION" \
    "voila chart version"

update_file "helm/keycloak-config-cli/Chart.yaml" \
    "^(version:) .+$" \
    "\\1 $HELM_VERSION" \
    "keycloak-config-cli chart version"

echo ""
echo "Helm values.yaml files (image.tag)..."
update_file "helm/launchpad/values.yaml" \
    "^(  tag:) .+$" \
    "\\1 $DOCKER_TAG" \
    "launchpad image.tag"

update_file "helm/voila/values.yaml" \
    "^(  tag:) .+$" \
    "\\1 $DOCKER_TAG" \
    "voila image.tag"

echo ""
echo "VERSION files..."
echo "$DOCKER_TAG" > "helm/jupyter/notebook/VERSION"
echo "  - VERSION file: helm/jupyter/notebook/VERSION"

echo "$DOCKER_TAG" > "helm/jupyter/embedding-notebook/VERSION"
echo "  - VERSION file: helm/jupyter/embedding-notebook/VERSION"

echo ""
echo "Version update complete!"
