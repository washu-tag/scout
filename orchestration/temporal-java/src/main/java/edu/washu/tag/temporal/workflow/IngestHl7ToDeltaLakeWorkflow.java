package edu.washu.tag.temporal.workflow;

import edu.washu.tag.temporal.model.IngestHl7FilesToDeltaLakeInput;
import edu.washu.tag.temporal.model.IngestHl7FilesToDeltaLakeOutput;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

@WorkflowInterface
public interface IngestHl7ToDeltaLakeWorkflow {
    @WorkflowMethod
    IngestHl7FilesToDeltaLakeOutput ingestHl7FileToDeltaLake(IngestHl7FilesToDeltaLakeInput input);
}
