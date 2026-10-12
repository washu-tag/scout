# Integration Tests

Integration tests for Scout are available within the [tests/ingest](../../tests/ingest) directory within the root of the repository. The tests are built
using [gradle](https://gradle.org/) and can be launched with the gradle wrapper script with a Java 21 JDK available:

```bash
$ cd tests/ingest
$ ./gradlew clean test
```

## Configuration

The tests require a JSON configuration file stored within `src/test/resources/config`. The configuration uses JSON
for compatibility with complex configuration requirements. If the name of a config file is passed to gradle with
`-Dconfig=<config name>`, the configuration will be read from the specified file. If it is left out, the tests will
attempt to load a default config in a `local.json`. The JSON configuration corresponds to a serialized version of
[TestConfig.java](../../tests/ingest/src/test/java/edu/washu/tag/TestConfig.java). Currently, the root-level properties are:
* `sparkConfig`: a dictionary that is passed as-is to spark in order to connect to the delta lake.
* `postgresConfig`: an [object](../../tests/ingest/src/test/java/edu/washu/tag/DatabaseConfig.java) defining `url`, `username`, and `password` with which to connect to Scout's postgres instance.
* `temporalConfig`: an optional [object](../../tests/ingest/src/test/java/edu/washu/tag/TemporalConfig.java) allowing overriding of some properties used in communicating with temporal. Child properties are:
    * `temporalUrl`: in-cluster URL with which the tests can access temporal. Defaults to the internal frontend, `temporal-internal-frontend.scout-extractor.svc:7236`, which skips the public frontend's JWT authorization.
    * `ingestJobInput`: an [object](../../tests/ingest/src/test/java/edu/washu/tag/model/IngestJobInput.java) passed to temporal to launch ingest.

## To run on a dev cluster

Assuming you are in the `scout` repo:
* Copy test data from `tests/ingest/staging_test_data/hl7` into the local directory you've mounted for the Extractors, e.g.,
```
cp -r tests/ingest/staging_test_data/hl7 ../data/
```
* Copy json config, make any modifications necessary for your set up
```
cp .github/ci_resources/test_config_template.json tests/ingest/src/test/resources/config/local.json
```
* Run the tests as a k8s job so they can talk to minio
```
sed "s:WORK_DIR:$(pwd):" .github/ci_resources/tests-job.yaml | kubectl apply -f -
kubectl -n extractor logs -f job/ci-tests
```

## On-prem Flux artifact proof

The [Post-Commit Tasks workflow](../../.github/workflows/ci.yaml) runs the on-prem
Flux ingest and authentication legs alongside the Ansible deployment tests, before
publishing build artifacts. Each leg installs its dependency graph on a fresh k3s
runner and checks config and site signatures, secret substitution and SOPS
decryption, image registry allowlists, and negative cases. The ingest leg deploys
the extractor's 16-component dependency closure and runs its integration suite.
The authentication leg deploys the 22-component closure of Launchpad, the on-prem
edge, read-only Trino, and the Keycloak fragment reconciler. The checked wait lists
and suspension policies must match those dependency closures.

The workflow freezes a signed predecessor haul and verifies that its source
revision is an ancestor of the tested checkout. The accumulated diff determines
which components to build from that checkout, including changes from earlier
failed or skipped builds. Unchanged components retain the predecessor's exact
digests. The same component catalog drives pull request and main builds.

The first build may encounter a legacy manifest with no source revision or build
provenance. That metadata triggers a full rebuild of every image and chart; its
haul is never pulled or carried, and no predecessor ancestry is claimed. The
plan records only the old manifest digest for audit and preserves existing vendor
aliases. After a signed build with provenance is published, normal verified carry
applies. Signature, registry, and malformed-provenance failures remain fatal.

A preparation job assembles one candidate containing those image references,
packaged charts, and stamped deployment config. Fresh images are pushed once to a
disposable registry and exported as OCI layouts. Each test runner copies the
candidate into its own registry without rebuilding images or repackaging charts
or config. k3s uses that registry as its `ghcr.io` mirror. Stamped HelmReleases
use native `chartRef` resources backed by `OCIRepository` sources pinned to chart
digests. The CI site points those sources at its temporary registry while
preserving each digest. Scout image references also retain their digests.

The candidate records the repository, revision, workflow run and attempt, version,
and artifact digests. Every consuming job validates this identity; artifacts from
another attempt cannot substitute for missing inputs. Both Flux legs use the
same config digest, verified against its raw OCI manifest and build annotations,
and signed with an ephemeral CI key. Config and site signing keys are independent.
Hauler remains the component manifest owner
([ADR 0033](adr/0033-build-lane-bundling-and-airgap-transport.md)).

On main, publication requires both Flux legs, the Ansible tests, and the existing
image scans and smoke tests to pass. Publication copies the candidate's OCI
content to GHCR, verifies that the digests are unchanged, and signs it with the
repository's release key. The haul bundle is assembled from those published
components. The aggregate `deploy-and-test` check includes both deployment paths;
there is no separate post-publication Flux workflow or advisory commit status.
A green pull request run proves that candidate's deployment, without publishing
it. A docs-only pull request can skip deployment when the accumulated plan also
has no deployable changes.

The config source copies its `application/gzip` layer unchanged; the default Flux
source extraction filters would otherwise remove packaged media such as the
sign-in logo. A Kustomize build failure stops the proof promptly with diagnostics.
After the predecessor snapshot is frozen, candidate assembly and publication do
not resolve a moving `:main` tag to select this build's components or config.

For an infrastructure failure, rerun all jobs in the CI workflow so the new
attempt builds and tests a complete candidate. Rerunning only failed jobs cannot
reuse the previous attempt's artifacts. Manual `Post-Commit Tasks` runs accept
`values=sops` or `values=plain`; the latter creates the values Secret directly in
the cluster. Both modes run both Flux legs. The legacy `haul-version` artifact
and Ansible deployment lane remain available during migration.

The site artifact uses its own ephemeral signing key, independent of the Scout
config key ([ADR 0031](adr/0031-gitops-deployment-base.md)). Both public keys are
bootstrapped directly into the cluster and stay outside the site artifact. The
workflow signs the site's exported OCI digest, verifies it with cosign, and pins
that same digest in Flux. After the site root creates the Scout config source,
the workflow waits for current-generation `Ready` and `SourceVerified` conditions on
both `scout-site` and `scout-config`, and verifies each source's expected repository
URL, digest pin, signer key, and resolved artifact revision. SOPS decryption and
missing-value negative cases remain part of the Scout deployment contract; this
proof does not duplicate Flux's own wrong-key or signature-replay conformance tests.

The site roots, artifact inputs, and CI overrides live in
`.github/ci_resources/flux/`. `cluster-vars.values.json` contains only the CI-specific
overrides; the workflow uses `jq` to recursively merge them with the deployment
tooling's `tooling/deploy/fixtures/cluster-vars.values.json` baseline before
validation and use.
The ingest fixture sets `minio_oidc_enabled` to `off` and `temporal_web_auth` to
`none`, and uses Temporal's internal frontend. The public frontend still requires
JWT authorization. Temporal UI OIDC is outside these tests.

The authentication runner reuses the bootstrap roles for Traefik, cert-manager,
and the internal CA. Its temporary ingress CA is explicitly trusted by the
browser, host, oauth2-proxy, Launchpad, and Traefik's forward-auth connection.
CI-specific site patches reduce resource requests and align the Keycloak/OPA
attribute-filter fixture; they do not alter the signed Scout config artifact.

The auth proof checks unauthenticated requests, sign-in by an unapproved user,
and approved access to Launchpad with a session cookie. It then seeds synthetic
Delta data using the transformer image selected from the exact tested config and
runs the existing data-authorization Jobs through Keycloak, OPA, and Trino.
The browser report and Job logs are retained for diagnosis. Resource requests
and observed memory usage are reported for both legs. This is a focused platform
proof; Superset, notebooks, chat, monitoring, and optional services are outside
these two dependency closures.

Run the fast identity and boundary checks without a cluster:

```bash
python3 -m pytest -q tooling/manifest tooling/deploy .github/ci_resources/flux \
  .github/scripts/test_*flux_artifacts.py
```

The certificate fixtures require OpenSSL with hostname verification (OpenSSL 3
in CI); macOS developers should put their installed OpenSSL 3 ahead of the system
LibreSSL on `PATH`. Install the pinned Flux CLI for the offline controller-render
checks; those checks skip when the CLI is unavailable.

The producer serializes main builds from predecessor selection through config
publication. This establishes component ancestry and binds the tested candidate
to one workflow attempt. Complete disconnected dependency transport and coverage
for the remaining platform services are separate milestones; these two Flux legs
do not establish a complete air-gapped installation. Release selection and
promotion are described in [Versions and Releases](versions-and-releases.md).

### SOPS admission guard

The Flux installation includes the same `deploy/bootstrap/sops-guard` Kustomize
base that ships as `bootstrap/sops-guard` in the config artifact. It is installed
before the site roots, and the negative case applies an encrypted values Secret
without `spec.decryption`. Reconciliation must fail and no Secret may be created.
The ordinary SOPS path must still decrypt the values and complete both proof legs.

With the pinned kustomize-controller v1.9.6, the no-decryption fixture was observed
to apply ciphertext unless the admission policy was installed. Secret normalization
removes the top-level `sops` metadata before the controller's encrypted-Secret check.
[Flux's original guard change](https://github.com/fluxcd/kustomize-controller/pull/483)
describes the intended early failure; it is not a tracking issue for this regression.
No matching upstream issue was found during review. Keep the policy until a fixed
controller has passed the no-decryption negative case without it, and track the
upstream regression separately before removing the workaround.

### Phase 3 transition to Flux as the default

The current proof is a migration step. The [Phase 3 transition plan](gitops-implementation-plan.md#transition-to-flux-as-the-default)
records the remaining coverage and upstream acceptance gates required before
restricting the Ansible lane to changes that still need it.
