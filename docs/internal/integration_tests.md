# Integration Tests

Integration tests for Scout are available within the [tests](../../tests) directory within the root of the repository. The tests are built
using [gradle](https://gradle.org/) and can be launched with the gradle wrapper script with a Java 21 JDK available:

```bash
$ cd tests
$ ./gradlew clean test
```

## Configuration

The tests require a JSON configuration file stored within `src/test/resources/config`. The configuration uses JSON
for compatibility with complex configuration requirements. If the name of a config file is passed to gradle with
`-Dconfig=<config name>`, the configuration will be read from the specified file. If it is left out, the tests will
attempt to load a default config in a `local.json`. The JSON configuration corresponds to a serialized version of
[TestConfig.java](../../tests/src/test/java/edu/washu/tag/TestConfig.java). Currently, the root-level properties are:
* `sparkConfig`: a dictionary that is passed as-is to spark in order to connect to the delta lake.
* `deltaLakeUrl`: the URL for the delta lake to be tested.

## To run on a dev cluster

Assuming you are in the `scout` repo:
* Copy test data from `tests/staging_test_data/hl7` into the local directory you've mounted for the temporal-java worker, e.g.,
```
cp -r tests/staging_test_data/hl7 ../data/
```
* Run `.github/ci_resources/launch_temporal_extraction.sh`
* Copy json config, make any modififations necessary for your set up
```
cp .github/ci_resources/test_config_template.json tests/src/test/resources/config/local.json
```
* Run the tests as a k8s job so they can talk to minio
```
sed "s:WORK_DIR:$(pwd):" .github/ci_resources/tests-job.yaml | kubectl apply -f -
kubectl -n explorer logs -f job/ci-tests
```
