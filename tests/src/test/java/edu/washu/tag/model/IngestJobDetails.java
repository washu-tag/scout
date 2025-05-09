package edu.washu.tag.model;

import io.temporal.api.common.v1.WorkflowExecution;
import io.temporal.api.enums.v1.WorkflowExecutionStatus;
import java.util.ArrayList;
import java.util.List;

public class IngestJobDetails {

    private String ingestWorkflowId;
    private List<String> ingestToDeltaLakeWorkflows = new ArrayList<>();
    private WorkflowExecutionStatus workflowExecutionStatus;
    private WorkflowExecution workflowExecution;

    public String getIngestWorkflowId() {
        return ingestWorkflowId;
    }

    public IngestJobDetails setIngestWorkflowId(String ingestWorkflowId) {
        this.ingestWorkflowId = ingestWorkflowId;
        return this;
    }

    public List<String> getIngestToDeltaLakeWorkflows() {
        return ingestToDeltaLakeWorkflows;
    }

    public IngestJobDetails setIngestToDeltaLakeWorkflows(List<String> ingestToDeltaLakeWorkflows) {
        this.ingestToDeltaLakeWorkflows = ingestToDeltaLakeWorkflows;
        return this;
    }

    public WorkflowExecutionStatus getWorkflowExecutionStatus() {
        return workflowExecutionStatus;
    }

    public IngestJobDetails setWorkflowExecutionStatus(WorkflowExecutionStatus workflowExecutionStatus) {
        this.workflowExecutionStatus = workflowExecutionStatus;
        return this;
    }

    public WorkflowExecution getWorkflowExecution() {
        return workflowExecution;
    }

    public IngestJobDetails setWorkflowExecution(WorkflowExecution workflowExecution) {
        this.workflowExecution = workflowExecution;
        return this;
    }

}
