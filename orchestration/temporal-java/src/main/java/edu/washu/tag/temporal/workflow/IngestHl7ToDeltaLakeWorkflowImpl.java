package edu.washu.tag.temporal.workflow;

import static edu.washu.tag.temporal.util.Constants.PYTHON_ACTIVITY;
import static edu.washu.tag.temporal.util.Constants.INGEST_DELTA_LAKE_QUEUE;

import edu.washu.tag.temporal.model.IngestHl7FilesToDeltaLakeInput;
import edu.washu.tag.temporal.model.IngestHl7FilesToDeltaLakeOutput;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.spring.boot.WorkflowImpl;
import io.temporal.workflow.ActivityStub;
import io.temporal.workflow.Workflow;
import io.temporal.workflow.WorkflowInfo;
import java.time.Duration;
import org.slf4j.Logger;

@WorkflowImpl(taskQueues = INGEST_DELTA_LAKE_QUEUE)
public class IngestHl7ToDeltaLakeWorkflowImpl implements IngestHl7ToDeltaLakeWorkflow {
    private static final Logger logger = Workflow.getLogger(IngestHl7ToDeltaLakeWorkflowImpl.class);

    private final ActivityStub ingestActivity =
        Workflow.newUntypedActivityStub(
            ActivityOptions.newBuilder()
                .setTaskQueue(INGEST_DELTA_LAKE_QUEUE)
                .setStartToCloseTimeout(Duration.ofMinutes(30))
                .setRetryOptions(RetryOptions.newBuilder()
                    .setMaximumInterval(Duration.ofSeconds(1))
                    .setMaximumAttempts(10)
                    .build())
                .build());

    @Override
    public IngestHl7FilesToDeltaLakeOutput ingestHl7FileToDeltaLake(IngestHl7FilesToDeltaLakeInput input) {
        WorkflowInfo workflowInfo = Workflow.getInfo();

        // TODO - Build manifest file if it isn't already present (improve launch UX for users)

        // Ingest HL7 into delta lake
        logger.info("WorkflowId {} - Launching activity to ingest HL7 files", workflowInfo.getWorkflowId());
        IngestHl7FilesToDeltaLakeOutput ingestHl7Output = ingestActivity.execute(
            PYTHON_ACTIVITY,
            IngestHl7FilesToDeltaLakeOutput.class,
            input
        );

        logger.info("WorkflowId {} - Activity complete, ingested {} HL7 files",
            workflowInfo.getWorkflowId(), ingestHl7Output.numHl7Ingested());

        return ingestHl7Output;
    }
}
