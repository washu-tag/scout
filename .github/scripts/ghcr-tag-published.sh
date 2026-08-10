#!/usr/bin/env bash
#
# Is ghcr.io/washu-tag/<image>:<tag> published?
#
# Usage:
#   GH_TOKEN=<token with packages:read> ghcr-tag-published.sh <image-name> <tag>
#
# Prints "true" or "false" and exits 0 when the registry gave a definitive
# answer; prints an ::error:: and exits non-zero when it did not, so no caller
# has to guess from an ambiguous result. Callers decide what a "false" means to
# them -- ci.yaml builds and publishes, release.yaml refuses to cut the release.
#
# Asks about the one tag rather than listing package versions: the packages API
# has no tag filter, and images carry 100+ versions (untagged ones are never
# pruned), so a list-and-grep only ever saw the newest page.
#
# The pull token must be authenticated. A package's default visibility is
# private, so an anonymous request cannot tell a private image from one that was
# never pushed and would report every newly published image as missing --
# which, for release.yaml, would block the release. Authenticated, a repo that
# does not exist simply 404s on the manifest like any missing tag, so there is no
# separate "no such repository" case to handle.
set -euo pipefail

IMAGE="${1:?usage: $0 <image-name> <tag>}"
TAG="${2:?usage: $0 <image-name> <tag>}"
: "${GH_TOKEN:?GH_TOKEN must be set to a token with packages:read}"

repo="washu-tag/${IMAGE}"

# --max-time bounds each attempt, NOT the sequence, so --retry-max-time bounds
# the sequence. curl retries timeouts, connection failures, 408/429/5xx -- never
# a 404, so a genuine "absent" still returns on the first try.
curl_opts=(-sS --max-time 10 --retry 3 --retry-connrefused --retry-delay 2 --retry-max-time 30)

token_body="$(mktemp)"
trap 'rm -f "${token_body}"' EXIT

# Credentials go in via stdin config, never argv.
token_code="$(printf 'user = "x:%s"\n' "${GH_TOKEN}" \
  | curl "${curl_opts[@]}" -K - -o "${token_body}" -w '%{http_code}' \
    "https://ghcr.io/token?service=ghcr.io&scope=repository:${repo}:pull")" || {
  echo "::error::could not reach the ghcr token endpoint for ${repo}" >&2
  exit 1
}
if [ "${token_code}" != '200' ]; then
  echo "::error::ghcr token endpoint returned HTTP ${token_code} for ${repo}" >&2
  exit 1
fi

# A manifest is a few KB of JSON listing layer digests, and HEAD transfers no
# body at all. Layers live under /v2/<repo>/blobs/ and are never touched here.
manifest_code="$(printf 'header = "Authorization: Bearer %s"\n' "$(jq -r '.token' "${token_body}")" \
  | curl "${curl_opts[@]}" -K - --head -o /dev/null -w '%{http_code}' \
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
