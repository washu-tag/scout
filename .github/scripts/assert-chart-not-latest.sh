#!/usr/bin/env bash
#
# Fail closed if a packaged chart still carries a placeholder in a published
# field: an `appVersion`, or an `image.tag` that `chart-app-version.sh` doesn't
# drive (e.g. an upstream sidecar). ADR 0030 section 2 requires that a Chart.yaml
# placeholder never reaches a published artifact, and that CI assert it.
# `stamp_config.py`'s `verify_clean` asserts this for the deploy tree; this
# asserts it for the chart defaults, which `verify_clean` never sees.
#
# Usage: assert-chart-not-latest.sh <packaged-chart.tgz> <chart-name>
#
# voila is exempt: it deploys the Scout scout-notebook image but its appVersion
# is Voila's own version, so its image tag can't fall back to appVersion (and
# `helm package` can't `--set` values); stamping it is a separate cleanup.
set -euo pipefail

TGZ="${1:?usage: $0 <packaged-chart.tgz> <chart-name>}"
NAME="${2:?usage: $0 <packaged-chart.tgz> <chart-name>}"

[ "$NAME" = "voila" ] && exit 0

# The bad set is stamp_config.py's _BAD_TAGS (latest, 0.0.0) plus the chart
# tree's 0.0.0-dev placeholder, so a chart added to a stamped group but not wired
# into chart-app-version.sh (which would ship a non-resolving default) fails here
# instead of silently. Capture first, then match a here-string: a piped `grep -q`
# can short-circuit and SIGPIPE the `helm show` writer, which under pipefail flips
# the pipeline non-zero and false-passes on a real match. The value is anchored so
# a real tag that merely contains one of these (latest-jre) still passes.
meta="$( { helm show chart "$TGZ"; helm show values "$TGZ"; } || true )"
if grep -qiE "^[[:space:]]*(appVersion|tag):[[:space:]]*['\"]?(latest|0\.0\.0-dev|0\.0\.0)['\"]?([[:space:]]|$)" <<<"$meta"; then
    echo "::error::${NAME} publishes a placeholder appVersion or image tag (latest / 0.0.0-dev / 0.0.0)"
    exit 1
fi
