package edu.washu.tag.temporal.workflow;

import edu.washu.tag.temporal.activity.SplitAndTransformHl7LogActivity;
import edu.washu.tag.temporal.model.Hl7FromHl7LogWorkflowInput;
import edu.washu.tag.temporal.model.Hl7FromHl7LogWorkflowOutput;
import edu.washu.tag.temporal.model.SplitAndTransformHl7LogInput;
import edu.washu.tag.temporal.model.SplitAndTransformHl7LogOutput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileInput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileOutput;
import edu.washu.tag.temporal.util.AllOfPromiseOnlySuccesses;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.failure.ApplicationFailure;
import io.temporal.spring.boot.WorkflowImpl;
import io.temporal.workflow.Workflow;
import io.temporal.workflow.WorkflowInfo;
import org.slf4j.Logger;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static edu.washu.tag.temporal.util.Constants.CHILD_QUEUE;

@WorkflowImpl(taskQueues = CHILD_QUEUE)
public class Hl7FromHl7LogWorkflowImpl implements Hl7FromHl7LogWorkflow {

    private static final Logger logger = Workflow.getLogger(Hl7FromHl7LogWorkflowImpl.class);

    private final SplitAndTransformHl7LogActivity hl7LogActivity =
            Workflow.newActivityStub(SplitAndTransformHl7LogActivity.class,
                    ActivityOptions.newBuilder()
                            .setStartToCloseTimeout(Duration.ofHours(1))
                            .setRetryOptions(RetryOptions.newBuilder()
                                    .setMaximumInterval(Duration.ofSeconds(1))
                                    .setMaximumAttempts(5)
                                    .build())
                            .build());

    @Override
    public Hl7FromHl7LogWorkflowOutput splitAndTransformHl7Log(Hl7FromHl7LogWorkflowInput input) {
        WorkflowInfo workflowInfo = Workflow.getInfo();
        logger.info("Beginning workflow {} workflowId {}", this.getClass().getSimpleName(), workflowInfo.getWorkflowId());

        // Validate input
        throwOnInvalidInput(input);

        // Split and transform log file
        String hl7RootPath = input.hl7OutputPath().replaceAll("/+$", "");
        SplitAndTransformHl7LogOutput splitAndTransformHl7LogOutput = hl7LogActivity.splitAndTransformHl7Log(new SplitAndTransformHl7LogInput(input.logPath(),
            hl7RootPath));

        // Write hl7 file paths to a file
        String scratchDir = input.scratchSpaceRootPath() + (input.scratchSpaceRootPath().endsWith("/") ? "" : "/") + workflowInfo.getWorkflowId();
        WriteHl7FilePathsFileOutput writeHl7FilePathsFileOutput = hl7LogActivity.writeHl7FilePathsFile(
            new WriteHl7FilePathsFileInput(splitAndTransformHl7LogOutput.paths(), scratchDir));

        logger.info("Completed workflow {} workflowId {}", this.getClass().getSimpleName(), workflowInfo.getWorkflowId());
        return new Hl7FromHl7LogWorkflowOutput(writeHl7FilePathsFileOutput.hl7FilesOutputFilePath(), writeHl7FilePathsFileOutput.numHl7Files());
    }

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

}
