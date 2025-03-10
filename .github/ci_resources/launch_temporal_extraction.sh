#!/bin/bash

echo "Submitting temporal workflow"

s3="s3://lake/orchestration"
deltalakepath="${s3}/delta/test_data"
hl7path="${s3}/hl7"
scratchpath="${s3}/scratch"
logspath="/data/hl7"
kubectl exec -n temporal -i service/temporal-admintools -- temporal workflow start \
  --task-queue ingest-hl7-log \
  --type IngestHl7LogWorkflow \
  --input '{"deltaLakePath":"'$deltalakepath'", "hl7OutputPath": "'$hl7path'", "scratchSpaceRootPath": "'$scratchpath'", "logsRootPath": "'$logspath'"}'

max_wait=300
for ((i = 0; i <= max_wait; ++i)); do
    if kubectl exec -n temporal -i service/temporal-admintools -- temporal workflow list -o json | jq '[.[] | select(.taskQueue == "ingest-hl7-log")] | all(.[]; .status == "WORKFLOW_EXECUTION_STATUS_COMPLETED") and length > 0' -e > /dev/null; then
        echo "All workflows completed as expected"
        kubectl exec -n temporal -i service/temporal-admintools -- temporal workflow list -o json
        exit 0
    else
        echo "Not all workflows completed, waiting and trying again..."
        kubectl exec -n temporal -i service/temporal-admintools -- temporal workflow list -o json
    fi

    sleep 1s
    if [[ i -eq max_wait ]]; then
        echo "DEBUGGING:"
        kubectl exec -n temporal -i service/temporal-admintools -- temporal workflow list -o json | jq -r '.[] | "\(.execution.workflowId) \(.execution.runId)"' | while read workflowId runId; do
            echo "Workflow id $workflowId and run id $runId"
            kubectl exec -n temporal -i service/temporal-admintools -- temporal workflow show --workflow-id $workflowId --run-id $runId
        done
        exit 25
    fi
done
