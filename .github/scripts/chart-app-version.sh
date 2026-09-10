#!/usr/bin/env bash
#
# Print the appVersion to stamp into a chart at package time, or nothing when the
# chart's own Chart.yaml appVersion stands.
#
# Usage: chart-app-version.sh <chart-name> <version> [<predecessor-haul.yaml>]
#   IMAGE_REBUILT=true  this run rebuilt the chart's Scout image, so it is published
#                       at <version> (ci.yaml sets it from the changes filter)
#
# appVersion is what a chart's image.tag falls back to (`default .Chart.AppVersion`),
# so it has to name an image that exists when the chart is published:
#   - Scout-image charts get the build that produced their image (ADR 0030 §2). On
#     the build lane that is <version> only when this run rebuilt the image
#     (IMAGE_REBUILT=true); otherwise the image was carried, and its tag is read
#     from the predecessor haul. The release lane publishes every Scout image at
#     <version>, so it passes no haul and gets <version>.
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

USAGE="Usage: $0 <chart-name> <version> [<predecessor-haul.yaml>]"
CHART="${1:?$USAGE}"
VERSION="${2:?$USAGE}"
HAUL="${3:-}"

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

fail() {
    echo "chart-app-version: $*" >&2
    exit 1
}

# The tag the chart's Scout image was built at.
scout_image_tag() {
    if [[ "${IMAGE_REBUILT:-false}" == "true" || -z "$HAUL" ]]; then
        printf '%s\n' "$VERSION"
        return
    fi
    [[ -f "$HAUL" ]] || fail "predecessor haul not found: $HAUL"
    local tag
    # Haul entries are `- name: ghcr.io/washu-tag/<image>:<tag>@sha256:...`; the
    # charts live under washu-tag/charts/, so this cannot match a chart entry.
    tag="$(sed -n -E "s#^ *- name: ghcr.io/washu-tag/$1:([^@ ]+)@sha256:.*#\1#p" "$HAUL" | head -1)"
    [[ -n "$tag" ]] || fail "ghcr.io/washu-tag/$1 is not in $HAUL; cannot resolve its appVersion"
    printf '%s\n' "$tag"
}

from_versions_yaml() {
    # Flat top-level `key: value` (optionally quoted); fail loudly on a missing key
    # rather than stamping an empty appVersion.
    local value
    value="$(sed -n -E "s/^$1: *[\"']?([^\"'# ]+)[\"']?.*$/\1/p" ansible/group_vars/all/versions.yaml | head -1)"
    [[ -n "$value" ]] || fail "no $1 in ansible/group_vars/all/versions.yaml"
    printf '%s\n' "$value"
}

case "$CHART" in
    hl7-transformer|hl7log-extractor|hl7-listener|launchpad|report-viewer)
        scout_image_tag "$CHART" ;;
    scout-opa)            from_versions_yaml opa_image_tag ;;
    hive-metastore)       from_versions_yaml hive_image_tag ;;
    temporal-bootstrap)   from_versions_yaml temporal_admin_tools_image_tag ;;
    keycloak-config-cli)  from_versions_yaml keycloak_config_cli_image_tag ;;
    # The import Job runs Scout's superset image, which CI tags from this file.
    scout-dashboards)     tr -d '[:space:]' < helm/superset/VERSION; echo ;;
    *) ;;
esac
