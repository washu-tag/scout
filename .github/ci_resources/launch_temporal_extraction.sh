#!/bin/bash

echo "Submitting temporal workflow"

s3="s3://lake/orchestration"
deltalakepath="${s3}/delta/test_data"
hl7path="${s3}/hl7"
scratchpath="${s3}/scratch"
logspath="/data/hl7"
json=$(kubectl exec -n temporal -i service/temporal-admintools -- temporal workflow start \
  --task-queue ingest-hl7-log \
  --type IngestHl7LogWorkflow \
  --input '{"deltaLakePath":"'$deltalakepath'", "hl7OutputPath": "'$hl7path'", "scratchSpaceRootPath": "'$scratchpath'", "logsRootPath": "'$logspath'"}' \
  -o json)

workflowId=$(echo $json | jq -r '.workflowId')

max_wait=60
for ((i = 0; i <= max_wait; ++i)); do
    workflowDetails=$(kubectl exec -n temporal -i service/temporal-admintools -- temporal workflow describe --workflow-id=$workflowId -o json)
    currentStatus=$(echo $workflowDetails | jq -r '.workflowExecutionInfo.status')
    if [[ $currentStatus == "WORKFLOW_EXECUTION_STATUS_COMPLETED" ]]; then
        echo "Workflow $workflowId completed as expected"
        exit 0
    else
        echo "Workflow not complete, waiting and trying again..."
        echo $workflowDetails
    fi

    sleep 5s
    if [[ i -eq max_wait ]]; then
        echo "DEBUGGING:"
        kubectl exec -n temporal -i service/temporal-admintools -- temporal workflow list -o json | jq -r '.[] | "\(.execution.workflowId) \(.execution.runId)"' | while read workflowId runId; do
            echo "Workflow id $workflowId and run id $runId"
            kubectl exec -n temporal -i service/temporal-admintools -- temporal workflow show --workflow-id $workflowId --run-id $runId
        done
        exit 25
    fi
done
