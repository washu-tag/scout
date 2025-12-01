package edu.washu.tag.extractor.hl7log.activity;

import static edu.washu.tag.extractor.hl7log.util.Constants.REFRESH_VIEWS_QUEUE;

import io.temporal.api.workflowservice.v1.ListWorkflowExecutionsRequest;
import io.temporal.api.workflowservice.v1.ListWorkflowExecutionsResponse;
import io.temporal.client.WorkflowClient;
import io.temporal.spring.boot.ActivityImpl;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Activity implementation to check for running transformer workflows.
 */
@Component
@ActivityImpl(taskQueues = REFRESH_VIEWS_QUEUE)
public class CheckRunningTransformersActivityImpl implements CheckRunningTransformersActivity {
    private static final Logger logger = LoggerFactory.getLogger(CheckRunningTransformersActivityImpl.class);

    private final WorkflowClient workflowClient;

    /**
     * Constructor for CheckRunningTransformersActivityImpl.
     *
     * @param workflowClient The Temporal workflow client.
     */
    public CheckRunningTransformersActivityImpl(WorkflowClient workflowClient) {
        this.workflowClient = workflowClient;
    }

    /**
     * Check if any IngestHl7ToDeltaLakeWorkflow instances are currently running.
     *
     * @return true if at least one transformer workflow is running, false otherwise.
     */
    @Override
    public boolean areTransformersRunning() {
        String query = "WorkflowType = 'IngestHl7ToDeltaLakeWorkflow' AND ExecutionStatus = 'Running'";

        ListWorkflowExecutionsRequest request = ListWorkflowExecutionsRequest.newBuilder()
            .setNamespace(workflowClient.getOptions().getNamespace())
            .setQuery(query)
            .setPageSize(1) // We only need to know if at least one exists
            .build();

        try {
            ListWorkflowExecutionsResponse response = workflowClient.getWorkflowServiceStubs()
                .blockingStub()
                .listWorkflowExecutions(request);

            boolean hasRunning = response.getExecutionsCount() > 0;
            logger.debug("Check for running transformers: {}", hasRunning);
            return hasRunning;
        } catch (Exception e) {
            logger.warn("Failed to check for running transformers, assuming none running", e);
            return false;
        }
    }
}
