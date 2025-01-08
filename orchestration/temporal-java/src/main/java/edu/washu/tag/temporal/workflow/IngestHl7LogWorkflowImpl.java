package edu.washu.tag.temporal.workflow;

import edu.washu.tag.temporal.model.IngestHl7LogWorkflowInput;
import edu.washu.tag.temporal.model.IngestHl7LogWorkflowOutput;
import io.temporal.spring.boot.WorkflowImpl;

@WorkflowImpl(workers = "split-hl7-log-worker")
public class IngestHl7LogWorkflowImpl implements IngestHl7LogWorkflow {
    @Override
    public IngestHl7LogWorkflowOutput ingestHl7Log(IngestHl7LogWorkflowInput input) {
        return null;
    }
}
