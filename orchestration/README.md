# Orchestration

# Build and Install
## Install Temporal
Deploy helm chart to kubernetes.
```shell
helm repo add temporal https://go.temporal.io/helm-charts
helm repo update temporal
kubectl create ns temporal
helm upgrade --install -n temporal temporal temporal/temporal --set-json server.additionalEnv='[{"TEMPORAL_CSRF_COOKIE_INSECURE":"true"}]'
```

The last option instructs Temporal to use the CSRF token even on non-TLS HTTP connections.
We need that setting to avoid errors in anything in the UI that modifies data.
But once we have a deploy with TLS and a reverse proxy, we will not need this setting.

## Build images
This will hopefully soon be unnecessary as we intend to build and deploy the images in CI.

```shell
docker build -t local/temporal-java:latest orchestration/temporal-java/
docker build -t local/temporal-python:latest orchestration/temporal-python/
```

## Launch Temporal Workers
Create kubernetes namespace if it doesn't already exist.
```shell
kubectl create ns orchestration-workers
```

Both the java and the python worker are deployed using the same helm chart with different values files.

```shell
helm upgrade --install -n orchestration-workers temporal-java helm/orchestration/temporal-worker -f helm/orchestration/temporal-java.values.yaml
helm upgrade --install -n orchestration-workers temporal-python helm/orchestration/temporal-worker -f helm/orchestration/temporal-python.values.yaml
```

# Launch a Workflow
## Make temporal alias
To interact with Temporal we use their CLI tools. But we don't need to install them locally, we need only `exec` a command into a pod that runs as part of the Temporal kubernetes installation which contains the CLI tools. Use your shell to alias this command to some name:
```shell
kubectl exec -n temporal -it service/temporal-admintools -- temporal
```
For instance, I use `fish` shell and I call my command `ktemporal` which looks like this:
```shell
alias ktemporal="kubectl exec -n temporal -it service/temporal-admintools -- temporal"
```
The syntax for other shells will be different, and you might call your command `temporal` or something different, but the general idea is the same.
## Prepare Input Values
The workflow has the following inputs:
- `deltaLakePath`: A local path or S3 URI where the delta lake files will be stored
- `hl7OutputPath`: A local path or S3 URI where HL7 files will be stored
- `scratchSpaceRootPath`: A local path or S3 URI where intermediate or "scratch" files will be stored
- `logsRootPath`: A local directory mounted into the worker pod where the input HL7 "log" files can be found
- `date`: The date of HL7 logs to look for and extract. Can be in `YYYYMMDD` or `YYYY-MM-DD` format.
## Use Alias to Launch Workflow
Assemble the input param values into a JSON object and pass it as a string to the temporal CLI alias.
```shell
ktemporal workflow start \
  --task-queue ingest-hl7-log \
  --type IngestHl7LogWorkflow \
  --input '{"deltaLakePath":"<deltaLakePath>", "hl7OutputPath":"<hl7OutputPath>", "scratchSpaceRootPath":"<scratchSpaceRootPath>", "logsRootPath": "<logsRootPath>", "date":"<YYYYMMDD>"}'
```
