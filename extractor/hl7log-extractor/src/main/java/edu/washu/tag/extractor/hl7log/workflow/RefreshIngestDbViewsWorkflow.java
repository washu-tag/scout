package edu.washu.tag.extractor.hl7log.workflow;

import edu.washu.tag.extractor.hl7log.model.IngestHl7LogWorkflowInput;
import edu.washu.tag.extractor.hl7log.model.IngestHl7LogWorkflowOutput;
import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsInput;
import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsOutput;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

@WorkflowInterface
public interface RefreshIngestDbViewsWorkflow {
    @WorkflowMethod
    RefreshIngestDbViewsOutput refreshIngestDbViews(RefreshIngestDbViewsInput input);
}
