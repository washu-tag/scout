#!/usr/bin/env bash
#
# Is ghcr.io/washu-tag/<image>:<tag> published?
#
# Usage:
#   ghcr-tag-published.sh <image-name> <tag>   # prints "true" or "false"
#
# Prints the answer and exits 0 when the registry gave a definitive one; prints
# an ::error:: and exits non-zero when it did not, so no caller has to guess
# from an ambiguous result. Callers decide what a "false" means to them --
# ci.yaml builds and publishes, release.yaml refuses to cut the release.
#
# Asks about the one tag rather than listing package versions: the packages API
# has no tag filter, and images carry 100+ versions (untagged ones are never
# pruned), so a list-and-grep only ever saw the newest page.
set -euo pipefail

IMAGE="${1:?usage: $0 <image-name> <tag>}"
TAG="${2:?usage: $0 <image-name> <tag>}"

repo="washu-tag/${IMAGE}"
# Retry first, so a hard failure below means a real problem rather than a blip:
# curl retries timeouts, connection failures, 408/429/5xx -- never 404.
retry=(--retry 3 --retry-connrefused --retry-delay 2)

token_body="$(mktemp)"
trap 'rm -f "${token_body}"' EXIT

# ghcr mints a pull token per repository. An image that has never been pushed
# has no repository, so the token endpoint answers 403 DENIED instead of issuing
# one -- that, not a 404 on the manifest, is what a brand-new image looks like.
token_code="$(curl -sS "${retry[@]}" -o "${token_body}" -w '%{http_code}' \
  "https://ghcr.io/token?service=ghcr.io&scope=repository:${repo}:pull")" || {
  echo "::error::could not reach the ghcr token endpoint for ${repo}" >&2
  exit 1
}
case "${token_code}" in
  200) ;;
  403)
    echo false
    exit 0
    ;;
  *)
    echo "::error::ghcr token endpoint returned HTTP ${token_code} for ${repo}" >&2
    exit 1
    ;;
esac

# A manifest is a few KB of JSON listing layer digests; HEAD transfers no body at
# all. Layers live under /v2/<repo>/blobs/ and are never touched here.
manifest_code="$(curl -sS "${retry[@]}" --head -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $(jq -r '.token' "${token_body}")" \
  -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json' \
  "https://ghcr.io/v2/${repo}/manifests/${TAG}")" || {
  echo "::error::could not reach ghcr for ${repo}:${TAG}" >&2
  exit 1
}
case "${manifest_code}" in
  200) echo true ;;
  404) echo false ;;
  *)
    echo "::error::ghcr returned HTTP ${manifest_code} for ${repo}:${TAG}; refusing to guess whether it is published" >&2
    exit 1
    ;;
esac
