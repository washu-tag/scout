package edu.washu.tag.extractor.hl7log.activity;

import static edu.washu.tag.extractor.hl7log.util.Constants.INGEST_DELTA_LAKE_QUEUE;
import static edu.washu.tag.extractor.hl7log.util.Constants.REFRESH_VIEWS_QUEUE;
import static edu.washu.tag.extractor.hl7log.util.Constants.VIEW_REFRESH_WORKFLOW_ID;

import edu.washu.tag.extractor.hl7log.workflow.RefreshIngestDbViewsWorkflow;
import io.temporal.client.BatchRequest;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowExecutionAlreadyStarted;
import io.temporal.client.WorkflowOptions;
import io.temporal.spring.boot.ActivityImpl;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Activity implementation to signal the view refresh entity workflow.
 * Starts the workflow if not running, then signals it.
 */
@Component
@ActivityImpl(taskQueues = INGEST_DELTA_LAKE_QUEUE)
public class SignalRefreshActivityImpl implements SignalRefreshActivity {
    private static final Logger logger = LoggerFactory.getLogger(SignalRefreshActivityImpl.class);

    private final WorkflowClient workflowClient;

    /**
     * Constructor for SignalRefreshActivityImpl.
     *
     * @param workflowClient The Temporal workflow client.
     */
    public SignalRefreshActivityImpl(WorkflowClient workflowClient) {
        this.workflowClient = workflowClient;
    }

    /**
     * Signal the view refresh entity workflow to request a refresh.
     * If the workflow is not running, it will be started first.
     *
     * @param sourceWorkflowId The ID of the workflow requesting the refresh.
     */
    @Override
    public void signalRefresh(String sourceWorkflowId) {
        WorkflowOptions options = WorkflowOptions.newBuilder()
            .setWorkflowId(VIEW_REFRESH_WORKFLOW_ID)
            .setTaskQueue(REFRESH_VIEWS_QUEUE)
            .build();

        RefreshIngestDbViewsWorkflow workflow = workflowClient.newWorkflowStub(
            RefreshIngestDbViewsWorkflow.class,
            options
        );

        // Use BatchRequest for signalWithStart to atomically either:
        // - Start a new workflow and then signal it, OR
        // - Signal the existing workflow if already running
        BatchRequest request = workflowClient.newSignalWithStartRequest();
        request.add(workflow::run);
        request.add(workflow::requestRefresh, sourceWorkflowId);
        workflowClient.signalWithStart(request);

        logger.info("Signaled view refresh workflow {} from workflow {}",
            VIEW_REFRESH_WORKFLOW_ID, sourceWorkflowId);
    }
}
