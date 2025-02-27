package edu.washu.tag.temporal.workflow;

import edu.washu.tag.temporal.activity.SplitHl7LogActivity;
import edu.washu.tag.temporal.model.FindHl7LogFileInput;
import edu.washu.tag.temporal.model.FindHl7LogFileOutput;
import edu.washu.tag.temporal.model.Hl7FromHl7LogWorkflowInput;
import edu.washu.tag.temporal.model.Hl7FromHl7LogWorkflowOutput;
import edu.washu.tag.temporal.model.SplitHl7LogActivityInput;
import edu.washu.tag.temporal.model.SplitHl7LogActivityOutput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogInput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogOutput;
import edu.washu.tag.temporal.util.WorkflowUtils;
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
import java.util.Deque;
import java.util.LinkedList;
import java.util.List;
import java.util.stream.Collectors;

@WorkflowImpl(taskQueues = "split-transform-hl7-log")
public class Hl7FromHl7LogWorkflowImpl implements Hl7FromHl7LogWorkflow {
    private static final Logger logger = Workflow.getLogger(Hl7FromHl7LogWorkflowImpl.class);

    private final SplitHl7LogActivity hl7LogActivity =
            Workflow.newActivityStub(SplitHl7LogActivity.class,
                    ActivityOptions.newBuilder()
                            .setStartToCloseTimeout(Duration.ofMinutes(5))
                            .setRetryOptions(RetryOptions.newBuilder()
                                    .setMaximumInterval(Duration.ofSeconds(1))
                                    .setMaximumAttempts(5)
                                    .build())
                            .build());

    @Override
    public Hl7FromHl7LogWorkflowOutput splitAndTransformHl7Log(Hl7FromHl7LogWorkflowInput input) {
        WorkflowInfo workflowInfo = Workflow.getInfo();
        logger.info("Beginning workflow {} workflowId {}", this.getClass().getSimpleName(), workflowInfo.getWorkflowId());

        // Log input values
        logger.debug("Input: {}", input);

        // Validate input
        throwOnInvalidInput(input);

        String scratchDir = input.scratchSpaceRootPath() + (input.scratchSpaceRootPath().endsWith("/") ? "" : "/") + workflowInfo.getWorkflowId();

        // Find log file by date
        FindHl7LogFileOutput findHl7LogFileOutput = hl7LogActivity.findHl7LogFile(new FindHl7LogFileInput(input.date(), input.logsRootPath()));

        // Split log file
        String splitLogFileOutputPath = scratchDir + "/split";
        SplitHl7LogActivityOutput splitHl7LogOutput = hl7LogActivity.splitHl7Log(new SplitHl7LogActivityInput(findHl7LogFileOutput.logFileAbsPath(), splitLogFileOutputPath));

        // Transform split logs into proper hl7 files
        String splitLogRootPath = splitHl7LogOutput.rootPath();
        String hl7RootPath = input.hl7OutputPath().endsWith("/") ? input.hl7OutputPath().substring(0, input.hl7OutputPath().length() - 1) : input.hl7OutputPath();
        Deque<Promise<TransformSplitHl7LogOutput>> transformSplitHl7LogOutputPromises = new LinkedList<>();
        for (String splitLogFileRelativePath : splitHl7LogOutput.relativePaths()) {
            // Async call to transform a single split log file into HL7
            String splitLogFilePath = splitLogRootPath + "/" + splitLogFileRelativePath;
            TransformSplitHl7LogInput transformSplitHl7LogInput = new TransformSplitHl7LogInput(splitLogFilePath, hl7RootPath);
            Promise<TransformSplitHl7LogOutput> transformSplitHl7LogOutputPromise =
                    Async.function(hl7LogActivity::transformSplitHl7Log, transformSplitHl7LogInput);
            transformSplitHl7LogOutputPromises.add(transformSplitHl7LogOutputPromise);
        }
        // Collect async results
        List<TransformSplitHl7LogOutput> transformSplitHl7LogOutputs = WorkflowUtils.getSuccessfulResults(transformSplitHl7LogOutputPromises, logger);
        if (transformSplitHl7LogOutputs.isEmpty()) {
            throw ApplicationFailure.newNonRetryableFailure("HL7 transformation failed", "type");
        }

        return new Hl7FromHl7LogWorkflowOutput(input.date(), transformSplitHl7LogOutputs.stream().map(TransformSplitHl7LogOutput::path).collect(Collectors.toList()));
    }

    private static void throwOnInvalidInput(Hl7FromHl7LogWorkflowInput input) {
        boolean hasLogsRootPath = input.logsRootPath() != null && !input.logsRootPath().isBlank();
        boolean hasScratchSpaceRootPath = input.scratchSpaceRootPath() != null && !input.scratchSpaceRootPath().isBlank();
        boolean hasHl7OutputPath = input.hl7OutputPath() != null && !input.hl7OutputPath().isBlank();
        boolean hasDate = input.date() != null && !input.date().isBlank();

        if (!(hasLogsRootPath && hasScratchSpaceRootPath && hasHl7OutputPath && hasDate)) {
            // We know something is missing
            List<String> missingInputs = new ArrayList<>();
            if (!hasLogsRootPath) {
                missingInputs.add("logsRootPath");
            }
            if (!hasScratchSpaceRootPath) {
                missingInputs.add("scratchSpaceRootPath");
            }
            if (!hasHl7OutputPath) {
                missingInputs.add("hl7OutputPath");
            }
            if (!hasDate) {
                missingInputs.add("date");
            }
            String plural = missingInputs.size() == 1 ? "" : "s";
            String missingInputsStr = String.join(", ", missingInputs);
            throw ApplicationFailure.newNonRetryableFailure("Missing required input" + plural + ": " + missingInputsStr, "type");
        }
    }

}
