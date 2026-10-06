#!/usr/bin/env bash
# ADR 0031 section 7: site configuration has its own signer, separate from Scout's key.
# Inputs: prepared $RUNNER_TEMP/site plus the local proof registry and build identity.
# Outputs: verified site digest and two harmless negative-fixture digests for Flux.
set -euo pipefail
: "${CI_REGISTRY:?}" "${VERSION:?}" "${RUNNER_TEMP:?}" "${TESTED_SHA:?}" "${GITHUB_REPOSITORY:?}" "${GITHUB_ENV:?}"
site="$RUNNER_TEMP/site"
trust="$RUNNER_TEMP/site-trust"
[ -d "$site" ] || { echo "::error::prepared site directory is missing"; exit 1; }
umask 077
mkdir -p "$trust"
chmod 700 "$trust"
cleanup() { rm -f "$trust/site.key" "$trust/wrong.key"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# This namespace is outside every packaged directory. Never touch RUNNER_TEMP/cosign.pub.
COSIGN_PASSWORD="$(openssl rand -hex 32)"
export COSIGN_PASSWORD
cosign generate-key-pair --output-key-prefix "$trust/site"
cosign generate-key-pair --output-key-prefix "$trust/wrong"
for pair in "scout-site-cosign-pub:site" "scout-site-wrong-pub:wrong"; do
  kubectl -n flux-system create secret generic "${pair%%:*}" \
    --from-file="cosign.pub=$trust/${pair#*:}.pub" --dry-run=client -o yaml | kubectl apply -f -
done

push_directory() { # <directory> <repository> <tag> <exported-manifest-stem>
  tar -C "$1" -czf "$trust/$4.tar.gz" .
  (cd "$trust" && oras push --plain-http "${CI_REGISTRY}/$2:$3" \
    "$4.tar.gz:application/gzip" \
    --annotation "org.opencontainers.image.source=https://github.com/${GITHUB_REPOSITORY}" \
    --annotation "org.opencontainers.image.revision=${TESTED_SHA}" \
    --annotation "org.opencontainers.image.version=${VERSION}" \
    --export-manifest "$trust/$4.manifest.json")
}
digest() { printf 'sha256:%s' "$(sha256sum "$1" | cut -d ' ' -f 1)"; }
sign() {
  cosign sign --key "$trust/site.key" --allow-http-registry \
    --use-signing-config=false --tlog-upload=false --yes "$1"
}
verify() {
  cosign verify --key "$1" --allow-http-registry --insecure-ignore-tlog=true "$2"
}

push_directory "$site" scout-site "$VERSION" scout-site
SITE_SOURCE="${CI_REGISTRY}/scout-site"
SITE_DIGEST="$(digest "$trust/scout-site.manifest.json")"
sign "${SITE_SOURCE}@${SITE_DIGEST}"
verify "$trust/site.pub" "${SITE_SOURCE}@${SITE_DIGEST}" > "$trust/site-verified.json"

# Both cases contain only a ConfigMap in a disposable namespace. The tampered case
# replays a genuinely valid signature over ORIGINAL content against DIFFERENT content.
for verdict in original tampered; do
  fixture="$trust/$verdict"
  mkdir -p "$fixture"
  cat > "$fixture/kustomization.yaml" <<'YAML'
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - marker.yaml
YAML
  cat > "$fixture/marker.yaml" <<YAML
apiVersion: v1
kind: ConfigMap
metadata:
  name: ci-site-trust-marker
  namespace: ci-site-negative
data:
  verdict: $verdict
YAML
done
push_directory "$trust/original" ci-site-negative "${VERSION}-good" site-good
push_directory "$trust/tampered" ci-site-negative "${VERSION}-tampered" site-tampered
SITE_GOOD_DIGEST="$(digest "$trust/site-good.manifest.json")"
SITE_TAMPERED_DIGEST="$(digest "$trust/site-tampered.manifest.json")"
[ "$SITE_GOOD_DIGEST" != "$SITE_TAMPERED_DIGEST" ] || { echo "::error::tampered fixture has unchanged digest"; exit 1; }
good="${CI_REGISTRY}/ci-site-negative@${SITE_GOOD_DIGEST}"
tampered="${CI_REGISTRY}/ci-site-negative@${SITE_TAMPERED_DIGEST}"
sign "$good"
verify "$trust/site.pub" "$good" > "$trust/good-verified.json"
cosign download signature --allow-http-registry "$good" > "$trust/good-signature.jsonl"
python3 - "$trust" "$SITE_GOOD_DIGEST" <<'PYBUNDLE'
import base64
import json
from pathlib import Path
import sys

trust = Path(sys.argv[1])
bundles = [json.loads(line) for line in (trust / "good-signature.jsonl").read_text().splitlines() if line.strip()]
if len(bundles) != 1 or bundles[0].get("mediaType") != "application/vnd.dev.sigstore.bundle.v0.3+json":
    raise SystemExit("expected exactly one modern Sigstore bundle from pinned cosign")
envelope = bundles[0]["dsseEnvelope"]
statement = json.loads(base64.b64decode(envelope["payload"], validate=True))
if [subject["digest"] for subject in statement["subject"]] != [{"sha256": sys.argv[2].removeprefix("sha256:")}]:
    raise SystemExit("fixture signature does not claim the original digest")
if len(envelope["signatures"]) != 1:
    raise SystemExit("fixture bundle must contain exactly one signature")
base64.b64decode(envelope["signatures"][0]["sig"], validate=True)
PYBUNDLE
# Cosign v3 emits a DSSE Sigstore bundle. Attach those exact bytes to a different
# OCI subject, without signing again or changing its signed statement/signature.
cosign attach signature --allow-http-registry --payload "$trust/good-signature.jsonl" "$tampered"
# Confirm the replay reached the registry; an absent signature is not this test.
cosign download signature --allow-http-registry "$tampered" > "$trust/tampered-signature.jsonl"
python3 - "$trust" <<'PYBUNDLE'
import json
from pathlib import Path
import sys

trust = Path(sys.argv[1])
original = json.loads((trust / "good-signature.jsonl").read_text())
replayed = [json.loads(line) for line in (trust / "tampered-signature.jsonl").read_text().splitlines() if line.strip()]
if replayed != [original]:
    raise SystemExit("tampered fixture is missing the exact original signature bundle")
PYBUNDLE
if verify "$trust/wrong.pub" "$good" > "$trust/wrong-key.log" 2>&1; then
  echo "::error::cosign accepted the site artifact under an unrelated key"
  exit 1
fi
if verify "$trust/site.pub" "$tampered" > "$trust/tampered.log" 2>&1; then
  echo "::error::cosign accepted an original signature replayed on tampered content"
  exit 1
fi
# Fail if a registry/network failure masked either intended cryptographic rejection.
grep -q 'accepted signatures do not match threshold' "$trust/wrong-key.log" || { cat "$trust/wrong-key.log"; exit 1; }
grep -q 'provided artifact digest does not match any digest in statement' "$trust/tampered.log" || { cat "$trust/tampered.log"; exit 1; }
# Publish outputs only after both positive checks and both rejection checks succeeded.
{
  echo "SITE_SOURCE=$SITE_SOURCE"
  echo "SITE_DIGEST=$SITE_DIGEST"
  echo "SITE_GOOD_DIGEST=$SITE_GOOD_DIGEST"
  echo "SITE_TAMPERED_DIGEST=$SITE_TAMPERED_DIGEST"
} >> "$GITHUB_ENV"
echo "Site artifact signature verified; CLI rejected wrong-key and changed-content signature replay fixtures."
