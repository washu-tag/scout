# Verifying Releases

Scout-owned container images, OCI Helm charts, config artifacts, haul manifests
and haul bundles are signed with [cosign](https://docs.sigstore.dev/). Releases
published through the tested-artifact promotion gate also attach a signed release
record that binds the exact source, CI attempt, required test profile and package
digests. Earlier releases may not have that record; a tag alone is not equivalent
evidence. Signing uses a **managed key** with the Sigstore transparency log
disabled, so you verify with a single public key and **no network access** to
Sigstore is required. See {ref}`why-keyed` below for the chosen offline verification model.

## The public key

The verification key is published in the repository root as
[`cosign.pub`](https://raw.githubusercontent.com/washu-tag/scout/main/cosign.pub).
It is the same key CI uses to sign, and it changes only if the signing key is
rotated. Establish it through your trusted bootstrap/change process, separately
from the release being verified. Download it on the connected side:

```bash
curl -fsSL -o cosign.pub https://raw.githubusercontent.com/washu-tag/scout/main/cosign.pub
```

## Verify the release record first

For a release with a signed release record, use the steps below. For a legacy
or maintenance-branch release, use
[per-artifact verification](#releases-without-a-release-record).

On the connected side, obtain the two assets from the selected published GitHub
Release. Replace the example version with the version you intend to install:

```bash
release_version=5.2.0
gh release download "v${release_version}" --repo washu-tag/scout \
  --pattern "scout-release-${release_version}.yaml" \
  --pattern "scout-release-${release_version}.sigstore.json" \
  --dir "release-${release_version}"
```

Transfer these assets and the package through your normal approved path. With
the independently provisioned `cosign.pub`, verify the record before using any
references inside it. The commands below use cosign 3.0.6, the pinned release
signer, and do not require access to Sigstore:

```bash
record="release-${release_version}/scout-release-${release_version}.yaml"
signature="release-${release_version}/scout-release-${release_version}.sigstore.json"
cosign verify-blob --key cosign.pub --insecure-ignore-tlog \
  --bundle "$signature" "$record"
jq -e --arg version "$release_version" \
  '.kind == "ScoutRelease" and .schemaVersion == 1 and .version == $version and .repository == "washu-tag/scout"' \
  "$record"
```

The `.yaml` asset uses JSON syntax, which is valid YAML 1.2 and can be read with
`jq`. The record identifies the CI run and attempt and the exact manifest,
bundle and config digests. Its test scope is the core on-prem ingest/authentication
profile, using the candidate config and images that CI published unchanged. The co-produced haul is signed; its restore and disconnected dependency
completeness are not certified by that test. Separately packaged release-version
charts remain listed as Ansible compatibility outputs, not as Flux-tested bytes.

## Verify and pin the recorded digests

Verify the exact package references from the authenticated record, rather than
resolving a mutable version tag again:

```bash
while IFS= read -r ref; do
  cosign verify --key cosign.pub --insecure-ignore-tlog "$ref" || exit 1
done < <(jq -r '.artifacts.manifestDigest, .artifacts.bundleDigest, .artifacts.configDigest' "$record")
```

`--insecure-ignore-tlog` skips the transparency-log check because these managed-key
signatures intentionally have no transparency-log entry; key/signature and
artifact-digest verification remain required. Registry access is still needed
to retrieve OCI content and signatures. In an enclave, use the relocated registry
content and preserved digests/signatures; no public Sigstore connection is needed.

Use the recorded config digest in the site's `OCIRepository.spec.ref.digest`
and retain `spec.verify` with the independently provisioned public key. The
release tag is a human-readable alias, not a replacement for the digest. Verify
legacy Ansible images/charts from the record's `.compatibility.images` and
`.compatibility.charts` when using that deployment path.

The Flux CI proof checks config and site signatures independently. A customer
site must configure its own trusted site signer; the ephemeral CI site key is
not a production trust root. The release signature uses the modern Sigstore bundle format emitted by the
pinned signer.

## Releases without a release record

Earlier releases and maintenance-branch releases have no signed release record.
Verify each artifact you intend to use directly with the independently
provisioned public key. Replace the example version with the published version
of that artifact; older releases may not include all of these artifact types.
If a release was meant to attach a record but it is missing, wait for the
maintainer to complete publication.

```bash
release_version=4.2.0

# A Helm chart
cosign verify --key cosign.pub --insecure-ignore-tlog \
  "ghcr.io/washu-tag/charts/hl7-transformer:${release_version}"

# A container image
cosign verify --key cosign.pub --insecure-ignore-tlog \
  "ghcr.io/washu-tag/hl7log-extractor:${release_version}"

# The config artifact
cosign verify --key cosign.pub --insecure-ignore-tlog \
  "ghcr.io/washu-tag/manifests/scout-config:${release_version}"

# The haul manifest and bundle
cosign verify --key cosign.pub --insecure-ignore-tlog \
  "ghcr.io/washu-tag/manifests/scout-manifest:${release_version}"
cosign verify --key cosign.pub --insecure-ignore-tlog \
  "ghcr.io/washu-tag/manifests/scout:${release_version}"
```

A successful check prints the signed payload and exits `0`. Retain its digest
and use a `repository@sha256:...` reference to pin the verified content. A missing
signature or failed verification is not evidence that an artifact is safe to
install. These checks verify artifact identity; they do not establish the
exact CI attempt and test profile certified by a signed release record.

## Before upgrading or rolling back

Record the installed config digest, release record and site-repo revision before
an upgrade. Read the release's upgrade notes and take the required application
and database backups before applying it, including a pre-upgrade PostgreSQL
backup when the release changes database-backed services. Include the relevant
MinIO/lake data, site configuration and secret/key recovery material in the
site's backup plan, and verify that the restore procedure is usable.

Reverting a site pin restores deployment configuration; it does not undo database
migrations, changed data, incompatible formats or rotated credentials. A release
signature proves identity, not downgrade safety. Follow component-specific
rollback limits; restore a compatible data/secret backup or use a forward fix
when a version cannot safely consume the current state. Resource prune guards
reduce accidental deletion but do not replace backups.

If release publication itself was interrupted, the maintainer can resume the
original identity by re-dispatching `release.yaml` with the same version.
Operators should wait for the published Release and verify its signed record
when present, rather than installing from a partial set of aliases.

(why-keyed)=
## Why a key, not keyless

Scout uses a managed key so operators can verify artifacts with an independently
provisioned public key and the existing Flux/Hauler toolchain. The key lifecycle
includes escrow, rotation and redistribution. Keyless verification can also work
offline when the signature bundle, inclusion proof and trusted roots travel with
the artifact; see [GitHub's offline verification guide](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/verify-attestations-offline).
That alternative is not the configured Scout trust model. The decision is
recorded in
[ADR 0033](https://github.com/washu-tag/scout/blob/main/docs/internal/adr/0033-build-lane-bundling-and-airgap-transport.md).

## Air-gapped verification

In an enclave, use the same keyed verification options against the relocated
registry references. The public key reaches the cluster through the staging-node trust
conduit (it is provisioned at bootstrap), **not** inside the haul: the haul
carries each artifact's cosign signature, and the key that checks those
signatures is delivered separately so the trust root never rides inside the
bundle it verifies. Transport must preserve artifact digests and their signatures.
After relocation into the enclave registry, `cosign verify --key cosign.pub
--insecure-ignore-tlog` checks those local references without public Sigstore access.
The current release proof does not certify haul restore or the full disconnected
transport flow described in [Air-Gapped Deployment](air-gapped.md).

## Regenerating the public key

The public key is derived from the signing key, so it can be regenerated at any
time without the private material. If the signing key is rotated, the
`verify-cosign-pubkey` CI check goes red until `cosign.pub` is refreshed. Run the
**Export cosign public key** workflow (`.github/workflows/export-cosign-pubkey.yaml`)
via *workflow_dispatch*: it derives the key and, if it differs from the committed
`cosign.pub`, opens a PR that refreshes the file (a no-op when it already
matches). Review and merge that PR to bring the check back to green.
