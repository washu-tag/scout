package edu.washu.tag.extractor.hl7log.workflow;

import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsInput;
import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsOutput;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

/**
 * Workflow for refreshing the database views used by the ingest dashboard after data has been ingested.
 */
@WorkflowInterface
public interface RefreshIngestDbViewsWorkflow {
    /**
     * Refreshes the database views.
     *
     * @param input The input parameters for refreshing the views.
     * @return The output of the refresh operation.
     */
    @WorkflowMethod
    RefreshIngestDbViewsOutput refreshIngestDbViews(RefreshIngestDbViewsInput input);
}
