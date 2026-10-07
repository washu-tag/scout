#!/usr/bin/env bash
# Resolve :main once, verify the exact manifest, and preserve its carry input.
# Registry/auth/not-found errors fail closed; they are never treated as bootstrap.
set -euo pipefail
snapshot="${1:?snapshot directory required}"
repo=ghcr.io/washu-tag/manifests/scout-manifest
mkdir -p "$snapshot"
oras manifest fetch "${repo}:main" --output "$snapshot/manifest.json"
legacy="$(PYTHONPATH="$(dirname "${BASH_SOURCE[0]}")/../../tooling/manifest" python3 - "$snapshot/manifest.json" <<'PYTHON'
import json
import sys
from producer_plan import legacy_manifest
with open(sys.argv[1]) as source:
    print(str(legacy_manifest(json.load(source))).lower())
PYTHON
)"
if [ "$legacy" = true ]; then
  # Old metadata cannot establish ancestry or supply trusted components.
  : > "$snapshot/haul.yaml"
  echo "::notice::Legacy predecessor has no provenance; rebuild every component without carrying its haul."
  exit 0
fi
digest="sha256:$(sha256sum "$snapshot/manifest.json" | cut -d ' ' -f 1)"
cosign verify --key cosign.pub --insecure-ignore-tlog "${repo}@${digest}" >/dev/null
oras pull "${repo}@${digest}" -o "$snapshot"
test -s "$snapshot/haul.yaml"
