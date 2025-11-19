# Report Ingestion

Handling of the data ingestion process is done by the {ref}`orchestrator service <orchestrator_ref>`. To submit the request,
any of the following means can pass the data on to Temporal:
1. Launching the workflow via Temporal's `admintools` container
2. Launching the workflow via Temporal's UI
3. Launching the workflow by connecting to Temporal via an SDK (done by the tests, but not recommended in production)

The format of the input is the same for the first two options, a JSON object providing the following properties. Some of the properties have
corresponding analogues available in Ansible variables. For these properties, omitting them will cause the ingest workflow to default to their
corresponding Ansible variables.
- `date`: an optional search parameter to filter to only the log file corresponding to the provided date.
   Format of the parameter should match the format of the date in the file name.
- `logPaths`: an optional list of specific log files to ingest. Can be absolute or relative to `logsRootPath`.
- `logsRootPath`: root path to search recursively for log files for ingest. Ansible equivalent: `hl7logs_root_dir`.
- `scratchSpaceRootPath`: root path to use for temporary files. The directory specified will be created if it does not exist.
   Ansible equivalent: `scratch_path`.
- `hl7OutputPath`: path to write HL7 files. Note that this is _not_ the path to the resulting delta lake. Ansible equivalent: `hl7_path`.
- `splitAndUploadTimeout`: timeout in minutes for the activity that splits the HL7 listener log files and uploads the component HL7 messages to MinIO. 
Ansible equivalent: `hl7log_extractor_timeout`
- `splitAndUploadHeartbeatTimeout`: timeout in minutes for **heartbeats** during the activity that splits the HL7 listener log files and uploads 
the component HL7 messages to MinIO. At a minimum, this is roughly the time to parse and upload one HL7 message, but it also includes steps like writing the manifest 
file and updating the database. (Note also: the temporal client only sends the heartbeats to the server periodically; in between sends, it queues up
heartbeats internally and could coalesce or drop heartbeats if they are too frequent.) Ansible equivalent: `hl7log_extractor_heartbeat_timeout`
- `splitAndUploadConcurrency`: number of HL7 listener log files to process concurrently. Ansible equivalent: `hl7log_extractor_concurrency`
- `modalityMapPath`: path to read modality map file, which is the source of the `modality` column in the Delta Lake table.
   This file is deployed as a Kubernetes ConfigMap from the source file specified by `modality_map_source_file` in the inventory.
   Ansible equivalent: `modality_map_path`.
- `reportTableName`: name of the Delta Lake table to write to. Ansible equivalent: `report_delta_table_name`.
- `deltaIngestTimeout`: timeout in minutes for the activity and transforms the HL7 and uploads to the delta lake. Ansible equivalent: `hl7_transformer_timeout` 

## admintools container

From the Kubernetes cluster, you can trigger an ingest job through Temporal's admintools container. An example invocation could look like:
```shell
kubectl exec -n temporal -i deployment/temporal-admintools -- temporal workflow start \
  --task-queue ingest-hl7-log \
  --type IngestHl7LogWorkflow \
  --input '{"logsRootPath": "/data/hl7", "reportTableName": "test_reports"}'
```

## Temporal UI

Alternatively, you can launch an ingestion job by clicking the "Start Workflow" button and filling out the form:
1. For "Workflow ID", enter a unique string or use the "Random UUID" button.
2. For "Task Queue", specify `ingest-hl7-log`.
3. For "Workflow Type", specify `IngestHl7LogWorkflow`.
4. For Input > Data, provide your input parameters, e.g. `{"logsRootPath": "/data/hl7", "reportTableName": "test_reports"}`.
5. For Input > Encoding, select `json/plain`.
