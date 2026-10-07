#!/usr/bin/env bash
# Verify the exact candidate config with the ephemeral CI signing key.
set -euo pipefail
oras_args=(manifest fetch)
cosign_args=(verify --key "$RUNNER_TEMP/cosign.pub" --insecure-ignore-tlog=true)
if [ "$CONFIG_INSECURE" = true ]; then
  oras_args+=(--plain-http)
  cosign_args+=(--allow-http-registry)
fi
python3 tooling/deploy/artifact_identity.py create \
  --repository "$IDENTITY_REPOSITORY" --revision "$TESTED_SHA" \
  --run-id "$IDENTITY_RUN_ID" --run-attempt "$IDENTITY_RUN_ATTEMPT" \
  --version "$VERSION" --manifest-digest "$MANIFEST_DIGEST" \
  --config-digest "$CONFIG_DIGEST" --output "$RUNNER_TEMP/scout-config-ref.json"
oras "${oras_args[@]}" --output "$RUNNER_TEMP/config-manifest.json" \
  "${CONFIG_SOURCE}@${CONFIG_DIGEST}"
python3 tooling/deploy/artifact_identity.py validate \
  --receipt "$RUNNER_TEMP/scout-config-ref.json" --manifest "$RUNNER_TEMP/config-manifest.json" \
  --repository "$IDENTITY_REPOSITORY" --revision "$TESTED_SHA" \
  --run-id "$IDENTITY_RUN_ID" --run-attempt "$IDENTITY_RUN_ATTEMPT"
# The keyed air-gap signature deliberately has no transparency-log dependency.
cosign "${cosign_args[@]}" "${CONFIG_SOURCE}@${CONFIG_DIGEST}" > "$RUNNER_TEMP/config-signature.json"
{
  echo "VERSION=$VERSION"
  echo "CONFIG_SOURCE=$CONFIG_SOURCE"
  echo "CONFIG_DIGEST=$CONFIG_DIGEST"
  echo "CONFIG_INSECURE=$CONFIG_INSECURE"
} >> "$GITHUB_ENV"
{
  echo "### Flux ${LEG:-ingest} proof: $ARTIFACT_MODE artifact"
  echo "- Repository: \`$IDENTITY_REPOSITORY\`"
  echo "- Revision: \`$TESTED_SHA\`"
  echo "- Producer run/attempt: \`$IDENTITY_RUN_ID/$IDENTITY_RUN_ATTEMPT\`"
  echo "- Config: \`${CONFIG_SOURCE}@${CONFIG_DIGEST}\`"
  echo "- Haul: \`${MANIFEST_REPO}@${MANIFEST_DIGEST}\`"
} >> "$GITHUB_STEP_SUMMARY"
