package edu.washu.tag.temporal.workflow;

import edu.washu.tag.temporal.activity.SplitHl7LogActivity;
import edu.washu.tag.temporal.model.Hl7FromHl7LogWorkflowInput;
import edu.washu.tag.temporal.model.Hl7FromHl7LogWorkflowOutput;
import edu.washu.tag.temporal.model.SplitHl7LogActivityInput;
import edu.washu.tag.temporal.model.SplitHl7LogActivityOutput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogInput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogOutput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileInput;
import edu.washu.tag.temporal.model.WriteHl7FilePathsFileOutput;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.failure.ApplicationFailure;
import io.temporal.spring.boot.WorkflowImpl;
import io.temporal.workflow.Async;
import io.temporal.workflow.Promise;
import io.temporal.workflow.Workflow;
import io.temporal.workflow.WorkflowInfo;
import org.slf4j.Logger;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@WorkflowImpl(taskQueues = "split-transform-hl7-log")
public class Hl7FromHl7LogWorkflowImpl implements Hl7FromHl7LogWorkflow {
    private static final Logger logger = Workflow.getLogger(Hl7FromHl7LogWorkflowImpl.class);

    private final SplitHl7LogActivity hl7LogActivity =
            Workflow.newActivityStub(SplitHl7LogActivity.class,
                    ActivityOptions.newBuilder()
                            .setStartToCloseTimeout(Duration.ofMinutes(1))
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

        String scratchDir = input.scratchSpaceRootPath() + (input.scratchSpaceRootPath().endsWith("/") ? "" : "/") + workflowInfo.getWorkflowId();

        // Split log file
        String splitLogFileRootPath = scratchDir + "/split";
        SplitHl7LogActivityOutput splitHl7LogOutput = hl7LogActivity.splitHl7Log(new SplitHl7LogActivityInput(input.logPath(), splitLogFileRootPath));

        // Transform split logs into proper hl7 files
        logger.info("WorkflowId {} - Launching {} async activities", workflowInfo.getWorkflowId(), splitHl7LogOutput.relativePaths().size());
        String hl7RootPath = input.hl7OutputPath().endsWith("/") ? input.hl7OutputPath().substring(0, input.hl7OutputPath().length() - 1) : input.hl7OutputPath();
        List<Promise<TransformSplitHl7LogOutput>> transformSplitHl7LogOutputPromises = new ArrayList<>();
        for (String splitLogFileRelativePath : splitHl7LogOutput.relativePaths()) {
            // Async call to transform a single split log file into HL7
            String splitLogFilePath = splitLogFileRootPath + "/" + splitLogFileRelativePath;
            TransformSplitHl7LogInput transformSplitHl7LogInput = new TransformSplitHl7LogInput(splitLogFilePath, hl7RootPath);
            Promise<TransformSplitHl7LogOutput> transformSplitHl7LogOutputPromise =
                    Async.function(hl7LogActivity::transformSplitHl7Log, transformSplitHl7LogInput);
            transformSplitHl7LogOutputPromises.add(transformSplitHl7LogOutputPromise);
        }
        // Collect async results
        logger.info("WorkflowId {} - Waiting for {} async activities to complete", workflowInfo.getWorkflowId(), transformSplitHl7LogOutputPromises.size());
        // List<TransformSplitHl7LogOutput> transformSplitHl7LogOutputs = new AllOfPromiseOnlySuccesses<>(transformSplitHl7LogOutputPromises).get();
        Promise.allOf(transformSplitHl7LogOutputPromises).get();
        List<TransformSplitHl7LogOutput> transformSplitHl7LogOutputs = transformSplitHl7LogOutputPromises.stream().map(Promise::get).toList();
        if (transformSplitHl7LogOutputs.isEmpty()) {
            throw ApplicationFailure.newNonRetryableFailure("HL7 transformation failed", "type");
        }

        logger.info("WorkflowId {} - Collecting results for {} successful async activities", workflowInfo.getWorkflowId(), transformSplitHl7LogOutputs.size());

        // Write hl7 file paths to a file
        List<String> hl7FilePaths = transformSplitHl7LogOutputs.stream().map(TransformSplitHl7LogOutput::path).toList();
        WriteHl7FilePathsFileOutput writeHl7FilePathsFileOutput = hl7LogActivity.writeHl7FilePathsFile(new WriteHl7FilePathsFileInput(hl7FilePaths, scratchDir));

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
