package edu.washu.tag.temporal.workflow;

import static edu.washu.tag.temporal.util.Constants.CHILD_QUEUE;

import edu.washu.tag.temporal.activity.SplitHl7LogActivity;
import edu.washu.tag.temporal.model.Hl7FromHl7LogWorkflowInput;
import edu.washu.tag.temporal.model.Hl7FromHl7LogWorkflowOutput;
import edu.washu.tag.temporal.model.SplitAndTransformHl7LogInput;
import edu.washu.tag.temporal.model.SplitAndTransformHl7LogOutput;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.failure.ApplicationFailure;
import io.temporal.spring.boot.WorkflowImpl;
import io.temporal.workflow.Workflow;
import io.temporal.workflow.WorkflowInfo;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;

@WorkflowImpl(taskQueues = CHILD_QUEUE)
public class Hl7FromHl7LogWorkflowImpl implements Hl7FromHl7LogWorkflow {

    private static final Logger logger = Workflow.getLogger(Hl7FromHl7LogWorkflowImpl.class);

    private final SplitHl7LogActivity hl7LogActivity =
        Workflow.newActivityStub(SplitHl7LogActivity.class,
            ActivityOptions.newBuilder()
                .setTaskQueue(CHILD_QUEUE)
                .setStartToCloseTimeout(Duration.ofHours(1))
                .setRetryOptions(RetryOptions.newBuilder()
                    .setMaximumInterval(Duration.ofSeconds(30))
                    .setMaximumAttempts(5)
                    .build())
                .build());

    private static void throwOnInvalidInput(Hl7FromHl7LogWorkflowInput input) {
        List<String> messages = new ArrayList<>();
        Map<String, String> requiredInputs = Map.of(
            "logPath", input.logPath(),
            "scratchSpacePath", input.scratchSpaceRootPath(),
            "hl7OutputPath", input.hl7OutputPath()
        );
        for (Map.Entry<String, String> entry : requiredInputs.entrySet()) {
            if (entry.getValue() == null || entry.getValue().isBlank()) {
                messages.add("Missing required input: " + entry.getKey());
            }
        }

        if (!messages.isEmpty()) {
            throw ApplicationFailure.newNonRetryableFailure(String.join("; ", messages), "type");
        }
    }

    @Override
    public Hl7FromHl7LogWorkflowOutput splitAndTransformHl7Log(Hl7FromHl7LogWorkflowInput input) {
        WorkflowInfo workflowInfo = Workflow.getInfo();
        logger.info("Beginning workflow {} workflowId {}", this.getClass().getSimpleName(), workflowInfo.getWorkflowId());

        // Validate input
        throwOnInvalidInput(input);

        // Split and transform log file
        String hl7RootPath = input.hl7OutputPath().replaceAll("/+$", "");
        String scratchDir = input.scratchSpaceRootPath() + (input.scratchSpaceRootPath().endsWith("/") ? "" : "/") + workflowInfo.getWorkflowId();
        SplitAndTransformHl7LogOutput splitAndTransformHl7LogOutput = hl7LogActivity.splitAndTransformHl7Log(new SplitAndTransformHl7LogInput(input.logPath(),
            hl7RootPath, scratchDir));

        logger.info("Completed workflow {} workflowId {}", this.getClass().getSimpleName(), workflowInfo.getWorkflowId());
        return new Hl7FromHl7LogWorkflowOutput(splitAndTransformHl7LogOutput.hl7FilesOutputFilePath(), splitAndTransformHl7LogOutput.numHl7Files());
    }

}
