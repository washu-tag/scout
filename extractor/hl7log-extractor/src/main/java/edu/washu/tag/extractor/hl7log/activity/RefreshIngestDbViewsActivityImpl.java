package edu.washu.tag.extractor.hl7log.activity;

import static edu.washu.tag.extractor.hl7log.util.Constants.REFRESH_VIEWS_PROCEDURE_NAME;
import static edu.washu.tag.extractor.hl7log.util.Constants.REFRESH_VIEWS_QUEUE;

import edu.washu.tag.extractor.hl7log.db.IngestDbService;
import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsInput;
import edu.washu.tag.extractor.hl7log.model.RefreshIngestDbViewsOutput;
import io.temporal.activity.Activity;
import io.temporal.activity.ActivityInfo;
import io.temporal.spring.boot.ActivityImpl;
import io.temporal.workflow.Workflow;
import org.slf4j.Logger;
import org.springframework.stereotype.Component;

@Component
@ActivityImpl(taskQueues = REFRESH_VIEWS_QUEUE)
public class RefreshIngestDbViewsActivityImpl implements RefreshIngestDbViewsActivity {
    private static final Logger logger = Workflow.getLogger(RefreshIngestDbViewsActivityImpl.class);

    private final IngestDbService ingestDbService;

    public RefreshIngestDbViewsActivityImpl(IngestDbService ingestDbService) {
        this.ingestDbService = ingestDbService;
    }

    @Override
    public RefreshIngestDbViewsOutput refreshIngestDbViews(RefreshIngestDbViewsInput input) {
        final ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
        final String workflowId = activityInfo.getWorkflowId();
        final String activityId = activityInfo.getActivityId();
        logger.info("WorkflowId {} ActivityId {} - Refreshing ingest views from database", workflowId, activityId);

        // Call the database service to refresh the views
        ingestDbService.callProcedure(REFRESH_VIEWS_PROCEDURE_NAME);
        return null;
    }
}
