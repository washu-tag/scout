package edu.washu.tag.extractor.hl7log.workflow;

import edu.washu.tag.extractor.hl7log.model.IngestHl7LogWorkflowInput;
import edu.washu.tag.extractor.hl7log.model.IngestHl7LogWorkflowOutput;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

/**
 * Workflow for ingesting HL7 log files.
 */
@WorkflowInterface
public interface IngestHl7LogWorkflow {
    /**
     * Ingests HL7 log files.
     *
     * @param input the input containing details for the HL7 log ingestion
     * @return the output of the HL7 log ingestion process
     */
    @WorkflowMethod
    IngestHl7LogWorkflowOutput ingestHl7Log(IngestHl7LogWorkflowInput input);
}
