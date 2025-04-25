# Orchestration

# Launch a Workflow via the CLI

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

# Launch a Workflow via the Temporal UI

## Prepare Input Values
See the Prepare Input Values section above.

## Launch Workflow
On the [Workflows](http://localhost:8080/namespaces/default/workflows) page, click "Start Workflow".

- Workflow ID: Click the "Random UUID" button
- Task Queue: `ingest-hl7-log`
- Workflow Type: `IngestHl7LogWorkflow`
- Input: Insert values into the input JSON `{"deltaLakePath":"<deltaLakePath>", "hl7OutputPath":"<hl7OutputPath>", "scratchSpaceRootPath":"<scratchSpaceRootPath>", "logsRootPath": "<logsRootPath>", "date":"<YYYYMMDD>"}` and paste that into the "Data" field.