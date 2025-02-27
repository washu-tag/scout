package edu.washu.tag.temporal.workflow;

import edu.washu.tag.temporal.model.Hl7FromHl7LogWorkflowInput;
import edu.washu.tag.temporal.model.Hl7FromHl7LogWorkflowOutput;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

@WorkflowInterface
public interface Hl7FromHl7LogWorkflow {
    @WorkflowMethod
    Hl7FromHl7LogWorkflowOutput splitAndTransformHl7Log(Hl7FromHl7LogWorkflowInput input);
}
