# Verifying Releases

Every published Scout artifact is signed with [cosign](https://docs.sigstore.dev/):
the container images, the OCI Helm charts, the config artifact, and the air-gap
haul bundle. Signing uses a **managed key** with the Sigstore transparency log
disabled, so you verify with a single public key and **no network access** to
Sigstore is required. This is what makes offline (air-gapped) verification
possible; see {ref}`why-keyed` below.

## The public key

The verification key is published in the repository root as
[`cosign.pub`](https://raw.githubusercontent.com/washu-tag/scout/main/cosign.pub).
It is the same key CI uses to sign, and it changes only if the signing key is
rotated. Download it once:

```bash
curl -fsSL -o cosign.pub https://raw.githubusercontent.com/washu-tag/scout/main/cosign.pub
```

## Verifying an artifact

Verify any signed reference with the public key and `--insecure-ignore-tlog`
(there is no transparency-log entry by design, so the flag is required, it does
not weaken the check):

```bash
# A Helm chart
cosign verify --key cosign.pub --insecure-ignore-tlog \
  ghcr.io/washu-tag/charts/hl7-transformer:4.2.0

# A container image
cosign verify --key cosign.pub --insecure-ignore-tlog \
  ghcr.io/washu-tag/hl7log-extractor:4.2.0

# The config artifact
cosign verify --key cosign.pub --insecure-ignore-tlog \
  ghcr.io/washu-tag/manifests/scout-config:4.2.0

# The air-gap haul bundle
cosign verify --key cosign.pub --insecure-ignore-tlog \
  ghcr.io/washu-tag/manifests/scout:4.2.0
```

A matching signature prints the verified payload and exits `0`; a wrong key or a
tampered artifact exits non-zero. In-cluster enforcement of the same signatures
through Flux `spec.verify` is the intended end state (ADR 0031/0033); it is not
yet wired into the deploy base, so today this manual check is how you confirm a
release before trusting it.

(why-keyed)=
## Why a key, not keyless

Scout signs with a managed key rather than keyless (Fulcio + Rekor) because an
air-gapped enclave cannot reach the Sigstore certificate authority or
transparency log. Keyed signing needs only the public key at verify time and
works fully offline, at the cost of having to distribute (and, on rotation,
re-distribute) that key. The rationale and the offline-verify mechanics are
recorded in
[ADR 0033](https://github.com/washu-tag/scout/blob/main/docs/internal/adr/0033-build-lane-bundling-and-airgap-transport.md).

## Air-gapped verification

In an enclave the same command works with no changes, because verification is
offline. The public key reaches the cluster through the staging-node trust
conduit (it is provisioned at bootstrap), **not** inside the haul: the haul
carries each artifact's cosign signature, and the key that checks those
signatures is delivered separately so the trust root never rides inside the
bundle it verifies. After `hauler store load` + `hauler store copy` relocate the
artifacts into the enclave registry, `cosign verify --key cosign.pub
--insecure-ignore-tlog` attests them offline. See [Air-Gapped
Deployment](air-gapped.md) for the full transport flow.

## Regenerating the public key

The public key is derived from the signing key, so it can be regenerated at any
time without the private material. If the signing key is rotated, the
`verify-cosign-pubkey` CI check goes red until `cosign.pub` is refreshed. Run the
**Export cosign public key** workflow (`.github/workflows/export-cosign-pubkey.yaml`)
via *workflow_dispatch*: it derives the key and, if it differs from the committed
`cosign.pub`, opens a PR that refreshes the file (a no-op when it already
matches). Review and merge that PR to bring the check back to green.
