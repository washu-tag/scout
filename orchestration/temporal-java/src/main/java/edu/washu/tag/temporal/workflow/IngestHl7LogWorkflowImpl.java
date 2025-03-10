package edu.washu.tag.temporal.workflow;

import edu.washu.tag.temporal.activity.FindHl7LogsActivity;
import edu.washu.tag.temporal.model.FindHl7LogFileInput;
import edu.washu.tag.temporal.model.FindHl7LogFileOutput;
import edu.washu.tag.temporal.model.Hl7FromHl7LogWorkflowInput;
import edu.washu.tag.temporal.model.Hl7FromHl7LogWorkflowOutput;
import edu.washu.tag.temporal.model.IngestHl7FilesToDeltaLakeInput;
import edu.washu.tag.temporal.model.IngestHl7FilesToDeltaLakeOutput;
import edu.washu.tag.temporal.model.IngestHl7LogWorkflowInput;
import edu.washu.tag.temporal.model.IngestHl7LogWorkflowOutput;
import edu.washu.tag.temporal.util.AllOfPromiseOnlySuccesses;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.common.SearchAttributeKey;
import io.temporal.failure.ApplicationFailure;
import io.temporal.spring.boot.WorkflowImpl;
import io.temporal.workflow.ActivityStub;
import io.temporal.workflow.Async;
import io.temporal.workflow.ChildWorkflowOptions;
import io.temporal.workflow.Promise;
import io.temporal.workflow.Workflow;
import io.temporal.workflow.WorkflowInfo;
import org.slf4j.Logger;

import java.nio.file.Path;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

import static edu.washu.tag.temporal.util.Constants.PARENT_QUEUE;
import static edu.washu.tag.temporal.util.Constants.CHILD_QUEUE;
import static edu.washu.tag.temporal.util.Constants.PYTHON_ACTIVITY;
import static edu.washu.tag.temporal.util.Constants.PYTHON_QUEUE;

@WorkflowImpl(taskQueues = PARENT_QUEUE)
public class IngestHl7LogWorkflowImpl implements IngestHl7LogWorkflow {
    private record ParsedLogInput(List<String> logPaths, String yesterday) { }

    private static final Logger logger = Workflow.getLogger(IngestHl7LogWorkflowImpl.class);

    private static final SearchAttributeKey<OffsetDateTime> SCHEDULED_START_TIME =
            SearchAttributeKey.forOffsetDateTime("TemporalScheduledStartTime");
    private static final DateTimeFormatter YYYYMMDD_FORMAT = DateTimeFormatter.ofPattern("yyyyMMdd");

    private final FindHl7LogsActivity findHl7LogsActivity =
            Workflow.newActivityStub(FindHl7LogsActivity.class,
                    ActivityOptions.newBuilder()
                            .setStartToCloseTimeout(Duration.ofSeconds(5))
                            .setRetryOptions(RetryOptions.newBuilder()
                                    .setMaximumInterval(Duration.ofSeconds(1))
                                    .setMaximumAttempts(3)
                                    .build())
                            .build());

    private final Hl7FromHl7LogWorkflow hl7FromHl7LogWorkflow =
            Workflow.newChildWorkflowStub(Hl7FromHl7LogWorkflow.class,
                    ChildWorkflowOptions.newBuilder()
                            .setTaskQueue(CHILD_QUEUE)
                            .build()
            );

    private final ActivityStub ingestActivity =
        Workflow.newUntypedActivityStub(
            ActivityOptions.newBuilder()
                    .setTaskQueue(PYTHON_QUEUE)
                    .setStartToCloseTimeout(Duration.ofMinutes(10))
                    .setRetryOptions(RetryOptions.newBuilder()
                            .setMaximumInterval(Duration.ofSeconds(1))
                            .setMaximumAttempts(10)
                            .build())
                    .build());

    @Override
    public IngestHl7LogWorkflowOutput ingestHl7Log(IngestHl7LogWorkflowInput input) {
        WorkflowInfo workflowInfo = Workflow.getInfo();
        logger.info("Beginning workflow {} workflowId {}", this.getClass().getSimpleName(), workflowInfo.getWorkflowId());

        // Parse / validate input
        ParsedLogInput parsedLogInput = parseInput(input);

        // Get final list of log paths
        FindHl7LogFileInput findHl7LogFileInput = new FindHl7LogFileInput(parsedLogInput.logPaths(), parsedLogInput.yesterday(), input.logsRootPath());
        FindHl7LogFileOutput findHl7LogFileOutput = findHl7LogsActivity.findHl7LogFiles(findHl7LogFileInput);

        // Launch child workflow for each log file
        logger.info("WorkflowId {} - Launching {} child workflows", workflowInfo.getWorkflowId(), findHl7LogFileOutput.logFiles().size());
        List<Promise<Hl7FromHl7LogWorkflowOutput>> childWorkflowOutputPromises = findHl7LogFileOutput.logFiles().stream()
                .map(logFile -> Async.function(
                        hl7FromHl7LogWorkflow::splitAndTransformHl7Log,
                        new Hl7FromHl7LogWorkflowInput(logFile, input.scratchSpaceRootPath(), input.hl7OutputPath())
                ))
                .toList();

        // Block workflow until all child workflows are complete or failed
        logger.info("WorkflowId {} - Waiting for {} child workflows to complete", workflowInfo.getWorkflowId(), childWorkflowOutputPromises.size());
        List<Hl7FromHl7LogWorkflowOutput> childWorkflowOutputs = new AllOfPromiseOnlySuccesses<>(childWorkflowOutputPromises).get();
        if (childWorkflowOutputs.isEmpty()) {
            throw ApplicationFailure.newNonRetryableFailure("All child workflows failed", "type");
        }


        logger.info("WorkflowId {} - Collecting results from {} successful child workflows", workflowInfo.getWorkflowId(), childWorkflowOutputs.size());

        // Collect HL7 file-path-file paths
        // This sounds more confusing than it is.
        // Each child workflow writes a single file with the paths of the HL7 files it created.
        // We collect the paths to these files (the contents of each being file paths) and pass them to the ingest activity.
        int[] numHl7FilesHolder = {0};
        List<String> hl7FilePathFiles = childWorkflowOutputs.stream()
                .peek(output -> numHl7FilesHolder[0] += output.numHl7Files())
                .map(Hl7FromHl7LogWorkflowOutput::hl7FilePathFile)
                .toList();

        // Ingest HL7 into delta lake
        logger.info("WorkflowId {} - Launching activity to ingest {} HL7 files",
                workflowInfo.getWorkflowId(), numHl7FilesHolder[0]);
        IngestHl7FilesToDeltaLakeOutput ingestHl7LogWorkflowOutput = ingestActivity.execute(
                PYTHON_ACTIVITY,
                IngestHl7FilesToDeltaLakeOutput.class,
                new IngestHl7FilesToDeltaLakeInput(input.deltaLakePath(), input.modalityMapPath(), hl7FilePathFiles)
        );

        logger.info("Completed workflow {} workflowId {}", this.getClass().getSimpleName(), workflowInfo.getWorkflowId());
        return new IngestHl7LogWorkflowOutput();
    }

    /**
     * Parse and validate input
     * @param input Workflow input
     * @return Log paths and "yesterday" date, if we are in a scheduled run
     */
    private static ParsedLogInput parseInput(IngestHl7LogWorkflowInput input) {

        // Need either log paths or logs root path
        boolean hasLogPathsInput = input.logPaths() != null && !input.logPaths().isBlank();
        boolean hasLogsRootPathInput = input.logsRootPath() != null && !input.logsRootPath().isBlank();
        List<String> logPaths;
        List<String> relativeLogPathsWithoutRoot = new ArrayList<>();
        if (hasLogPathsInput) {
            logPaths = new ArrayList<>();
            Path logsRootPath = hasLogsRootPathInput ? Path.of(input.logsRootPath()) : null;
            for (String logPath : input.logPaths().split(",")) {
                if (logPath.startsWith("/")) {
                    logPaths.add(logPath);
                } else if (hasLogsRootPathInput) {
                    logPaths.add(logsRootPath.resolve(logPath).toString());
                } else {
                    relativeLogPathsWithoutRoot.add(logPath);
                }
            }
        } else if (hasLogsRootPathInput) {
            // If we have a logs root path, we will ingest all logs from that path
            logPaths = List.of(input.logsRootPath());
        } else {
            logPaths = Collections.emptyList();
        }

        // If we are in a scheduled run and we were not given any explicit log paths
        //   we will ingest logs from "yesterday"
        //   which we define as the day before the scheduled time in the local timezone
        // Note: There isn't a good API to find the scheduled start time in the SDK. We have to use a
        //  search attribute.
        // See https://docs.temporal.io/workflows#action for docs on the search attribute.
        // See also https://github.com/temporalio/features/issues/243 where someone asks
        //  for a better API for this in the SDK.
        OffsetDateTime scheduledTimeUtc = Workflow.getTypedSearchAttributes().get(SCHEDULED_START_TIME);
        boolean hasScheduledTime = scheduledTimeUtc != null;
        String yesterday = null;
        if (hasScheduledTime && !hasLogPathsInput) {
                ZoneId localTz = ZoneOffset.systemDefault();
                OffsetDateTime scheduledTimeLocal = scheduledTimeUtc.atZoneSameInstant(localTz).toOffsetDateTime();
                OffsetDateTime yesterdayDt = scheduledTimeLocal.minusDays(1);
                yesterday = yesterdayDt.format(YYYYMMDD_FORMAT);
                logger.info("Using date {} from scheduled workflow start time {} ({} in TZ {}) minus one day", yesterday, scheduledTimeUtc, scheduledTimeLocal, localTz);
        }

        // Now we have enough information to throw for invalid inputs
        throwOnInvalidInput(input, hasLogPathsInput, hasLogsRootPathInput, relativeLogPathsWithoutRoot, hasScheduledTime);

        return new ParsedLogInput(logPaths, yesterday);
    }

    private static void throwOnInvalidInput(IngestHl7LogWorkflowInput input, boolean hasLogPaths, boolean hasLogRootPath, List<String> relativeLogPaths, boolean hasScheduledTime) {
        List<String> messages = new ArrayList<>();

        // Always required
        Map<String, String> requiredInputs = Map.of(
                "scratchSpacePath", input.scratchSpaceRootPath(),
                "hl7OutputPath", input.hl7OutputPath(),
                "deltaLakePath", input.deltaLakePath()
        );
        for (Map.Entry<String, String> entry : requiredInputs.entrySet()) {
            if (entry.getValue() == null || entry.getValue().isBlank()) {
                messages.add("Missing required input: " + entry.getKey());
            }
        }

        // Any relative log paths?
        if (!relativeLogPaths.isEmpty()) {
            messages.add("Can only use relative logPaths with logsRootPath. Invalid log paths: " + String.join(", ", relativeLogPaths));
        }

        // Need either log paths or logs root path
        if (!hasLogPaths && !hasLogRootPath) {
            messages.add("Must provide either logPaths or logsRootPath");
        }

        // If we are in a scheduled run, we need logs root path
        if (hasScheduledTime && !hasLogRootPath) {
            messages.add("Must provide logsRootPath for scheduled runs");
        }

        if (!messages.isEmpty()) {
            throw ApplicationFailure.newNonRetryableFailure(String.join("; ", messages), "type");
        }
    }
}
