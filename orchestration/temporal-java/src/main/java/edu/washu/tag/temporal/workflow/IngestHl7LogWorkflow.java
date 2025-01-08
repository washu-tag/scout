package edu.washu.tag.temporal.workflow;

import edu.washu.tag.temporal.model.IngestHl7LogWorkflowInput;
import edu.washu.tag.temporal.model.IngestHl7LogWorkflowOutput;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

@WorkflowInterface
public interface IngestHl7LogWorkflow {
    @WorkflowMethod
    IngestHl7LogWorkflowOutput ingestHl7Log(IngestHl7LogWorkflowInput input);
}
