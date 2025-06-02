package edu.washu.tag.extractor.hl7log.workflow;

import static edu.washu.tag.extractor.hl7log.util.Constants.REFRESH_VIEWS_HEARTBEAT_INTERVAL_SECONDS;
import static edu.washu.tag.extractor.hl7log.util.Constants.REFRESH_VIEWS_QUEUE;
import static edu.washu.tag.extractor.hl7log.util.Constants.REFRESH_VIEWS_TIMEOUT_MINUTES;

import edu.washu.tag.extractor.hl7log.activity.RefreshIngestDbViewsActivity;
import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsInput;
import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsOutput;
import io.temporal.activity.ActivityCancellationType;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.spring.boot.WorkflowImpl;
import io.temporal.workflow.Workflow;
import java.time.Duration;

@WorkflowImpl(taskQueues = REFRESH_VIEWS_QUEUE)
public class RefreshIngestDbViewsWorkflowImpl implements RefreshIngestDbViewsWorkflow {

    private final RefreshIngestDbViewsActivity refreshIngestDbViewsActivity =
        Workflow.newActivityStub(RefreshIngestDbViewsActivity.class,
            ActivityOptions.newBuilder()
                .setStartToCloseTimeout(Duration.ofMinutes(REFRESH_VIEWS_TIMEOUT_MINUTES))
                .setHeartbeatTimeout(Duration.ofSeconds(REFRESH_VIEWS_HEARTBEAT_INTERVAL_SECONDS * 2))
                .setCancellationType(ActivityCancellationType.WAIT_CANCELLATION_COMPLETED)
                .setRetryOptions(RetryOptions.newBuilder()
                    .setMaximumInterval(Duration.ofMinutes(1))
                    .setMaximumAttempts(2)
                    .build())
                .build());

    @Override
    public RefreshIngestDbViewsOutput refreshIngestDbViews(RefreshIngestDbViewsInput input) {
        return refreshIngestDbViewsActivity.refreshIngestDbViews(input);
    }
}