package edu.washu.tag.extractor.hl7log.workflow;

import edu.washu.tag.extractor.hl7log.model.ViewRefreshStatus;
import io.temporal.workflow.QueryMethod;
import io.temporal.workflow.SignalMethod;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

/**
 * Workflow for refreshing the database views used by the ingest dashboard after data has been ingested.
 *
 * <p>This workflow operates as a long-running entity that receives refresh requests via signals.
 * Multiple signals while a refresh is running are debounced - only one additional refresh will be queued.
 */
@WorkflowInterface
public interface RefreshIngestDbViewsWorkflow {
    /**
     * Main workflow method that runs the entity loop.
     * Waits for signals and executes refreshes.
     */
    @WorkflowMethod
    void run();

    /**
     * Signal to request a view refresh.
     * If a refresh is already running, one additional refresh will be queued.
     * Multiple signals while running are debounced.
     *
     * @param sourceWorkflowId The ID of the workflow requesting the refresh.
     */
    @SignalMethod
    void requestRefresh(String sourceWorkflowId);

    /**
     * Query the current status of the refresh workflow.
     *
     * @return The current status including whether a refresh is running and pending.
     */
    @QueryMethod
    ViewRefreshStatus getStatus();
}
