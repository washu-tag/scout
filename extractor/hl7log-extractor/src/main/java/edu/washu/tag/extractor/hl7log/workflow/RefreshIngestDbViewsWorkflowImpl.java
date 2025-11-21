package edu.washu.tag.extractor.hl7log.workflow;

import edu.washu.tag.extractor.hl7log.activity.CheckRunningTransformersActivity;
import edu.washu.tag.extractor.hl7log.activity.RefreshIngestDbViewsActivity;
import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsInput;
import edu.washu.tag.extractor.hl7log.model.ViewRefreshStatus;
import edu.washu.tag.extractor.hl7log.util.Constants;
import io.temporal.activity.ActivityCancellationType;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.spring.boot.WorkflowImpl;
import io.temporal.workflow.Workflow;
import java.time.Duration;
import org.slf4j.Logger;

/**
 * Entity workflow implementation for refreshing database views.
 *
 * <p>Operates as a long-running workflow that receives refresh requests via signals.
 * Uses a boolean flag to ensure at most one refresh is queued at any time.
 */
@WorkflowImpl(taskQueues = Constants.REFRESH_VIEWS_QUEUE)
public class RefreshIngestDbViewsWorkflowImpl implements RefreshIngestDbViewsWorkflow {
    private static final Logger logger = Workflow.getLogger(RefreshIngestDbViewsWorkflowImpl.class);
    private static final Duration REFRESH_VIEWS_HEARTBEAT_TIMEOUT = Duration.ofSeconds(Constants.REFRESH_VIEWS_HEARTBEAT_INTERVAL_SECONDS * 2);
    private static final Duration IDLE_TIMEOUT = Duration.ofHours(1);

    // Workflow state - these do NOT need to be Atomic because Temporal workflows
    // are single-threaded. All code in a workflow (including signal handlers)
    // executes on the same workflow thread, never concurrently.
    private boolean pendingRefresh = false;
    private boolean isRefreshing = false;
    private int totalRefreshes = 0;

    private final RefreshIngestDbViewsActivity refreshActivity =
        Workflow.newActivityStub(RefreshIngestDbViewsActivity.class,
            ActivityOptions.newBuilder()
                .setHeartbeatTimeout(REFRESH_VIEWS_HEARTBEAT_TIMEOUT)
                .setCancellationType(ActivityCancellationType.WAIT_CANCELLATION_COMPLETED)
                .setStartToCloseTimeout(Duration.ofHours(Constants.REFRESH_VIEWS_TIMEOUT_HOURS))
                .setRetryOptions(RetryOptions.newBuilder()
                    // Give the activity a chance to heartbeat and figure out it has timed out before retrying
                    // When an activity times out it gets notified only during a heartbeat, and delivery
                    // of the cancellation can be delayed up to 80% of the heartbeat timeout.
                    // See https://community.temporal.io/t/problems-cancelling-a-running-activity-from-parent-workflow/2169
                    // To compensate for this, do not start retrying until the full heartbeat timeout has elapsed.
                    .setInitialInterval(REFRESH_VIEWS_HEARTBEAT_TIMEOUT)
                    .setMaximumAttempts(2)
                    .build())
                .build());

    // Activity stub for checking running transformer workflows
    private final CheckRunningTransformersActivity checkRunningTransformersActivity =
        Workflow.newActivityStub(CheckRunningTransformersActivity.class,
            ActivityOptions.newBuilder()
                .setStartToCloseTimeout(Duration.ofMinutes(1))
                .setRetryOptions(RetryOptions.newBuilder()
                    .setMaximumAttempts(3)
                    .build())
                .build());

    @Override
    public void requestRefresh(String sourceWorkflowId) {
        logger.info("Received refresh request from workflow {}, isRefreshing={}, pendingRefresh={}",
            sourceWorkflowId, isRefreshing, pendingRefresh);
        pendingRefresh = true;
    }

    @Override
    public ViewRefreshStatus getStatus() {
        return new ViewRefreshStatus(isRefreshing, pendingRefresh, totalRefreshes);
    }

    @Override
    public void run() {
        while (true) {
            // Check if Temporal suggests we should continue-as-new to reset history
            if (Workflow.getInfo().isContinueAsNewSuggested()) {
                logger.info("Continue-as-new suggested, resetting workflow history after {} refreshes", totalRefreshes);
                Workflow.continueAsNew();
            }

            // If we've done work and have no pending refresh, check if we should exit
            // (Skip this check on first iteration to give signals a chance to arrive)
            if (totalRefreshes > 0 && !pendingRefresh) {
                boolean transformersRunning = checkRunningTransformersActivity.areTransformersRunning();
                if (!transformersRunning) {
                    logger.info("No pending work and no transformers running - completing after {} refreshes",
                        totalRefreshes);
                    return;
                }
            }

            // Wait for a refresh request or timeout
            boolean hasWork = Workflow.await(IDLE_TIMEOUT, () -> pendingRefresh);

            if (!hasWork) {
                // Timed out - check if transformers are still running
                boolean transformersRunning = checkRunningTransformersActivity.areTransformersRunning();
                if (transformersRunning) {
                    logger.debug("No pending work but transformer workflows are running, continuing to wait");
                    continue;
                }
                logger.info("Workflow idle timeout with no transformers running - completing after {} refreshes",
                    totalRefreshes);
                return;
            }

            // Execute refresh
            isRefreshing = true;
            pendingRefresh = false;

            try {
                logger.info("Starting view refresh #{}", totalRefreshes + 1);
                refreshActivity.refreshIngestDbViews(new RefreshIngestDbViewsInput());
                totalRefreshes++;
                logger.info("View refresh completed, total={}", totalRefreshes);
            } catch (Exception e) {
                logger.error("View refresh failed", e);
                throw e;
            } finally {
                isRefreshing = false;
            }
        }
    }
}
