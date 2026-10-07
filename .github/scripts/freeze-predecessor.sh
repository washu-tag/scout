#!/usr/bin/env bash
# Resolve :main once, verify the exact manifest, and preserve its carry input.
# Registry/auth/not-found errors fail closed; they are never treated as bootstrap.
set -euo pipefail
snapshot="${1:?snapshot directory required}"
repo=ghcr.io/washu-tag/manifests/scout-manifest
mkdir -p "$snapshot"
oras manifest fetch "${repo}:main" --output "$snapshot/manifest.json"
digest="sha256:$(sha256sum "$snapshot/manifest.json" | cut -d ' ' -f 1)"
cosign verify --key cosign.pub --insecure-ignore-tlog "${repo}@${digest}" >/dev/null
oras pull "${repo}@${digest}" -o "$snapshot"
test -s "$snapshot/haul.yaml"
