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
    * `temporalUrl`: in-cluster URL with which the tests can access temporal. Defaults to `temporal-frontend.temporal.svc:7233`.
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
