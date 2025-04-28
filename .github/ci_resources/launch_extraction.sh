#!/bin/bash

echo "Submitting temporal workflow"

s3="s3://lake"
deltalakepath="${s3}/delta"
hl7path="${s3}/hl7"
scratchpath="${s3}/scratch"
logspath="/data/hl7"
json=$(kubectl exec -n temporal -i deployment/temporal-admintools -- temporal workflow start \
  --task-queue ingest-hl7-log \
  --type IngestHl7LogWorkflow \
  --input '{"deltaLakePath":"'$deltalakepath'", "hl7OutputPath": "'$hl7path'", "scratchSpaceRootPath": "'$scratchpath'", "logsRootPath": "'$logspath'"}' \
  -o json)

workflowId=$(echo $json | jq -r '.workflowId')

max_wait=60
for ((i = 0; i <= max_wait; ++i)); do
    workflowDetails=$(kubectl exec -n temporal -i deployment/temporal-admintools -- temporal workflow describe --workflow-id=$workflowId -o json)
    currentStatus=$(echo $workflowDetails | jq -r '.workflowExecutionInfo.status')
    if [[ $currentStatus == "WORKFLOW_EXECUTION_STATUS_COMPLETED" ]]; then
        echo "Workflow $workflowId completed as expected"
        break
    elif [[ $currentStatus == "WORKFLOW_EXECUTION_STATUS_FAILED" ]]; then
        echo "Workflow $workflowId failed"
        exit 1
    else
        echo "Workflow $workflowId status=\"$currentStatus\". Waiting..."
    fi

    sleep 5s
    if [[ i -eq max_wait ]]; then
        echo "DEBUGGING:"
        kubectl exec -n temporal -i deployment/temporal-admintools -- temporal workflow list -o json | jq -r '.[] | "\(.execution.workflowId) \(.execution.runId)"' | while read workflowId runId; do
            echo "Workflow id $workflowId and run id $runId"
            kubectl exec -n temporal -i deployment/temporal-admintools -- temporal workflow show --workflow-id $workflowId --run-id $runId
        done
        exit 25
    fi
done

echo "Waiting for all child workflows to be complete"
childWorkflows=$(kubectl exec -n temporal -i deployment/temporal-admintools -- temporal workflow list --query "WorkflowType = 'IngestHl7ToDeltaLakeWorkflow'" -o json | jq -r '.[].execution.workflowId')
for ((i = 0; i <= max_wait; ++i)); do
  pendingChildWorkflows=$(kubectl exec -n temporal -i deployment/temporal-admintools -- temporal workflow list --query "WorkflowType = 'IngestHl7ToDeltaLakeWorkflow' and ExecutionStatus not in ('Completed', 'Failed')" -o json | jq -r '.[].execution.workflowId')
  if [[ -z "$pendingChildWorkflows" ]]; then
      echo "All child workflows complete"
      exit 0
  else
      echo "Pending child workflows:"
      echo $pendingChildWorkflows
      echo "Waiting..."
      sleep 5s
  fi
done
echo "Waited too long"
exit 1
