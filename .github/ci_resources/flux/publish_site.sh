#!/usr/bin/env bash
# ADR 0031 section 7: site configuration has its own signer, separate from Scout's key.
# Inputs: prepared $RUNNER_TEMP/site plus the local proof registry and build identity.
# Outputs: the signed site's exact OCI source and digest for Flux.
set -euo pipefail
: "${CI_REGISTRY:?}" "${VERSION:?}" "${RUNNER_TEMP:?}" "${TESTED_SHA:?}" "${GITHUB_REPOSITORY:?}" "${GITHUB_ENV:?}"
site="$RUNNER_TEMP/site"
trust="$RUNNER_TEMP/site-trust"
[ -d "$site" ] || { echo "::error::prepared site directory is missing"; exit 1; }
umask 077
mkdir -p "$trust"
chmod 700 "$trust"
cleanup() { rm -f "$trust/site.key"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Bootstrap the independent site key outside every packaged directory.
# Never touch RUNNER_TEMP/cosign.pub, which verifies the Scout config.
COSIGN_PASSWORD="$(openssl rand -hex 32)"
export COSIGN_PASSWORD
cosign generate-key-pair --output-key-prefix "$trust/site"
kubectl -n flux-system create secret generic scout-site-cosign-pub \
  --from-file="cosign.pub=$trust/site.pub" --dry-run=client -o yaml | kubectl apply -f -

tar -C "$site" -czf "$trust/scout-site.tar.gz" .
(cd "$trust" && oras push --plain-http "${CI_REGISTRY}/scout-site:${VERSION}" \
  "scout-site.tar.gz:application/gzip" \
  --annotation "org.opencontainers.image.source=https://github.com/${GITHUB_REPOSITORY}" \
  --annotation "org.opencontainers.image.revision=${TESTED_SHA}" \
  --annotation "org.opencontainers.image.version=${VERSION}" \
  --export-manifest "$trust/scout-site.manifest.json")
SITE_SOURCE="${CI_REGISTRY}/scout-site"
SITE_DIGEST="sha256:$(sha256sum "$trust/scout-site.manifest.json" | cut -d ' ' -f 1)"
cosign sign --key "$trust/site.key" --allow-http-registry \
  --use-signing-config=false --tlog-upload=false --yes "${SITE_SOURCE}@${SITE_DIGEST}"
cosign verify --key "$trust/site.pub" --allow-http-registry --insecure-ignore-tlog=true \
  "${SITE_SOURCE}@${SITE_DIGEST}" > "$trust/site-verified.json"
{
  echo "SITE_SOURCE=$SITE_SOURCE"
  echo "SITE_DIGEST=$SITE_DIGEST"
} >> "$GITHUB_ENV"
echo "Site artifact signature verified."
