package edu.washu.tag.extractor.hl7log.workflow;

import static edu.washu.tag.extractor.hl7log.util.Constants.REFRESH_VIEWS_HEARTBEAT_INTERVAL_SECONDS;
import static edu.washu.tag.extractor.hl7log.util.Constants.REFRESH_VIEWS_QUEUE;
import static edu.washu.tag.extractor.hl7log.util.Constants.REFRESH_VIEWS_TIMEOUT_HOURS;

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
    private final static int REFRESH_VIEWS_HEARTBEAT_TIMEOUT_SECONDS = REFRESH_VIEWS_HEARTBEAT_INTERVAL_SECONDS * 2;

    private final RefreshIngestDbViewsActivity refreshIngestDbViewsActivity =
        Workflow.newActivityStub(RefreshIngestDbViewsActivity.class,
            ActivityOptions.newBuilder()
                .setHeartbeatTimeout(Duration.ofSeconds(REFRESH_VIEWS_HEARTBEAT_TIMEOUT_SECONDS))
                .setCancellationType(ActivityCancellationType.WAIT_CANCELLATION_COMPLETED)
                .setRetryOptions(RetryOptions.newBuilder()
                    // Give the activity a chance to heartbeat and figure out it has timed out before retrying
                    // When an activity times out it gets notified only during a heartbeat, and delivery
                    // of the cancellation can be delayed up to 80% of the heartbeat timeout.
                    // See https://community.temporal.io/t/problems-cancelling-a-running-activity-from-parent-workflow/2169
                    // To compensate for this, do not start retrying until the full heartbeat timeout has elapsed.
                    .setInitialInterval(Duration.ofSeconds(REFRESH_VIEWS_HEARTBEAT_TIMEOUT_SECONDS))
                    .setMaximumAttempts(2)
                    .build())
                .build());

    @Override
    public RefreshIngestDbViewsOutput refreshIngestDbViews(RefreshIngestDbViewsInput input) {
        return refreshIngestDbViewsActivity.refreshIngestDbViews(input);
    }
}