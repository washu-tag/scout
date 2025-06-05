package edu.washu.tag.extractor.hl7log.workflow;

import edu.washu.tag.extractor.hl7log.model.IngestHl7FilesToDeltaLakeInput;
import edu.washu.tag.extractor.hl7log.model.IngestHl7FilesToDeltaLakeOutput;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

/**
 * Workflow for ingesting HL7 files into Delta Lake.
 */
@WorkflowInterface
public interface IngestHl7ToDeltaLakeWorkflow {
    /**
     * Ingests HL7 files into Delta Lake.
     *
     * @param input Input parameters for the ingestion workflow.
     * @return Output of the ingestion process.
     */
    @WorkflowMethod
    IngestHl7FilesToDeltaLakeOutput ingestHl7FileToDeltaLake(IngestHl7FilesToDeltaLakeInput input);
}
