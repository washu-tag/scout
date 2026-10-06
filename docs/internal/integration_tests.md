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

The [Flux workflow](../../.github/workflows/deploy-flux.yaml) installs the on-prem
ingest dependency graph on a fresh k3s runner and runs the ingest tests against it.
It checks config and site signatures, secret substitution and SOPS decryption,
image registry allowlists, and negative cases before accepting the result. The ingest graph
does not exercise the full platform or its public authentication paths.

There are two artifact modes:

* **Published:** after a successful upstream `Post-Commit Tasks` push to `main`,
  the consumer selects `scout-config-ref-<run_attempt>` from that exact producer
  run. It checks out the producer commit, validates the receipt and signed OCI
  manifest, and gives Flux the same config repository and digest. Only the
  isolated status job can write `on-prem/flux-ingest` against the producer commit.
* **Local:** deployment-related pull requests, prototype pushes, and manual runs
  build a config from the proposed checkout and a snapshot of the released haul.
  An ephemeral key signs this config. The same manifest/identity verifier and
  Flux reconciliation path run, but this is not evidence that the producer
  published a release from the proposed commit. Manual runs accept `values=sops`
  or `values=plain`; the latter creates the values Secret directly in the cluster.

The receipt is build identity metadata, not another component inventory. Hauler
remains the component manifest owner ([ADR 0033](adr/0033-build-lane-bundling-and-airgap-transport.md)).
Its schema records the repository, revision, producer run and attempt, version,
haul manifest digest, and config digest. The raw OCI manifest must match the config
digest, and its signed annotations must match the build identity fields.
Registry locations are fixed in the workflows. Neither
the producer's config stamping nor the published consumer resolves a moving
`:main` tag to select this build's haul/config.

Missing receipts skip the published proof (for example, a build that did not
publish a config). Duplicate, expired, malformed, or mismatched receipts fail;
API and download errors also fail. Reruns use a new attempt-specific receipt and
are checked against their own signed annotations. The legacy `haul-version`
artifact and Ansible deployment lane remain available during migration.

The site artifact uses its own ephemeral signing key, independent of the Scout
config key ([ADR 0031](adr/0031-gitops-deployment-base.md)). Both public keys are
bootstrapped directly into the cluster and stay outside the site artifact. The
workflow signs the site's exported OCI digest, verifies it with cosign, and pins
that same digest in Flux. It checks current-generation `Ready` and `SourceVerified`
conditions and the resolved OCI revision for both sources.

Two additional negative cases use harmless ConfigMap fixtures in a separate
namespace: a valid bundle checked with the wrong key, and the unchanged original
signature bundle attached to modified content. The publisher checks that the
replayed bundle is actually present and that cosign rejects the intended failures.
Flux must reject both sources with a verification error, publish no usable
artifact, and apply no marker. Test resources are cleaned up even after a failure.
The test is gated before ingest deployment and exercises the modern Sigstore bundle
format emitted by the pinned cosign version; it does not certify legacy formats.

Run the fast identity and boundary checks without a cluster:

```bash
python3 -m pytest -q tooling/deploy .github/ci_resources/flux/test_*.py
```

This proof is one part of the release gate. The producer still carries unchanged
components from a previous haul; concurrent build ordering and component ancestry
need a separate solution. Complete
disconnected dependency transport, full-platform/authentication tests, and release
promotion remain follow-up work. The advisory commit status can be replaced by
another attempt for the same commit; promotion must check the specific producer
attempt and config digest, rather than this commit status alone.
The automatic published path requires this
workflow on the upstream default branch and a new producer receipt; a green fork
run exercises the local mode only.
