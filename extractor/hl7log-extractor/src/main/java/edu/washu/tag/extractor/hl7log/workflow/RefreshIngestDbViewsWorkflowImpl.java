package edu.washu.tag.extractor.hl7log.workflow;

import java.time.Duration;

import edu.washu.tag.extractor.hl7log.activity.RefreshIngestDbViewsActivity;
import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsInput;
import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsOutput;
import static edu.washu.tag.extractor.hl7log.util.Constants.REFRESH_VIEWS_QUEUE;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.spring.boot.WorkflowImpl;
import io.temporal.workflow.Workflow;

@WorkflowImpl(taskQueues = REFRESH_VIEWS_QUEUE)
public class RefreshIngestDbViewsWorkflowImpl implements RefreshIngestDbViewsWorkflow {

    private final RefreshIngestDbViewsActivity refreshIngestDbViewsActivity =
        Workflow.newActivityStub(RefreshIngestDbViewsActivity.class,
            ActivityOptions.newBuilder()
                .setStartToCloseTimeout(Duration.ofHours(3))
                .setRetryOptions(RetryOptions.newBuilder()
                    .setMaximumInterval(Duration.ofSeconds(60))
                    .setMaximumAttempts(3)
                    .build())
                .build());

    @Override
    public RefreshIngestDbViewsOutput refreshIngestDbViews(RefreshIngestDbViewsInput input) {
        return refreshIngestDbViewsActivity.refreshIngestDbViews(input);
    }
}