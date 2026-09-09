#!/usr/bin/env bash
#
# Print the appVersion to stamp into a chart at package time, or nothing when the
# chart's own Chart.yaml appVersion stands.
#
# Usage: chart-app-version.sh <chart-name> <version-being-published>
#
# appVersion is what a chart's image.tag falls back to (`default .Chart.AppVersion`),
# so it has to name an image that exists at publish time:
#   - Scout-image charts get the version being published; their image is
#     published at that same tag.
#   - Upstream-wrapping charts get the pin CI already deploys and hauls: the
#     Renovate-tracked value in ansible/group_vars/all/versions.yaml, or the
#     VERSION file CI tags the image with. Their Chart.yaml carries a
#     non-resolving 0.0.0-dev placeholder so a stale literal can never ship.
#   - Everything else (voila, dcm4chee, orthanc, open-webui-bootstrap) keeps the
#     appVersion its Chart.yaml declares: no output, no --app-version flag.
#
# This is the one place that maps a chart to its appVersion source; ci.yaml,
# release.yaml, and seed-charts.yaml all call it.
set -euo pipefail

CHART="${1:?Usage: $0 <chart-name> <version>}"
VERSION="${2:?Usage: $0 <chart-name> <version>}"

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

from_versions_yaml() {
    # Flat top-level `key: value` (optionally quoted); fail loudly on a missing key
    # rather than stamping an empty appVersion.
    local value
    value="$(sed -n -E "s/^$1: *[\"']?([^\"'# ]+)[\"']?.*$/\1/p" ansible/group_vars/all/versions.yaml | head -1)"
    if [[ -z "$value" ]]; then
        echo "chart-app-version: no $1 in ansible/group_vars/all/versions.yaml" >&2
        exit 1
    fi
    printf '%s\n' "$value"
}

case "$CHART" in
    hl7-transformer|hl7log-extractor|hl7-listener|launchpad|report-viewer)
        printf '%s\n' "$VERSION" ;;
    scout-opa)            from_versions_yaml opa_image_tag ;;
    hive-metastore)       from_versions_yaml hive_image_tag ;;
    temporal-bootstrap)   from_versions_yaml temporal_admin_tools_image_tag ;;
    keycloak-config-cli)  from_versions_yaml keycloak_config_cli_image_tag ;;
    # The import Job runs Scout's superset image, which CI tags from this file.
    scout-dashboards)     tr -d '[:space:]' < helm/superset/VERSION; echo ;;
    *) ;;
esac
