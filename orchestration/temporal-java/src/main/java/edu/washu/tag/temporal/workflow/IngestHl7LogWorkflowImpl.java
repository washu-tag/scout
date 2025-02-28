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
import edu.washu.tag.temporal.util.WorkflowUtils;
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
import java.util.Deque;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@WorkflowImpl(taskQueues = "ingest-hl7-log")
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
                            .setTaskQueue("split-transform-hl7-log")
                            .build()
            );

    private static final String INGEST_ACTIVITY_NAME = "ingest_hl7_files_to_delta_lake_activity";
    private final ActivityStub ingestActivity =
        Workflow.newUntypedActivityStub(
            ActivityOptions.newBuilder()
                    .setTaskQueue("ingest-hl7-delta-lake")
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
        Deque<Promise<Hl7FromHl7LogWorkflowOutput>> childWorkflowOutputs = findHl7LogFileOutput.logFiles().stream()
                .map(logFile -> Async.function(
                        hl7FromHl7LogWorkflow::splitAndTransformHl7Log,
                        new Hl7FromHl7LogWorkflowInput(logFile, input.scratchSpaceRootPath(), input.hl7OutputPath())
                ))
                .collect(Collectors.toCollection(LinkedList::new));
        List<Hl7FromHl7LogWorkflowOutput> childWorkflowResults = WorkflowUtils.getSuccessfulResults(childWorkflowOutputs, logger);
        if (childWorkflowResults.isEmpty()) {
            throw ApplicationFailure.newNonRetryableFailure("Child workflows failed", "type");
        }

        // Collect HL7 paths
        List<String> hl7Paths = childWorkflowResults.stream()
                .map(Hl7FromHl7LogWorkflowOutput::hl7Paths)
                .flatMap(List::stream)
                .toList();

        // Ingest HL7 into delta lake
        logger.info("WorkflowId {} - Launching activity to ingest {} HL7 files",
                workflowInfo.getWorkflowId(), hl7Paths.size());
        IngestHl7FilesToDeltaLakeOutput ingestHl7LogWorkflowOutput = ingestActivity.execute(
                INGEST_ACTIVITY_NAME,
                IngestHl7FilesToDeltaLakeOutput.class,
                new IngestHl7FilesToDeltaLakeInput(input.deltaLakePath(), input.modalityMapPath(), hl7Paths)
        );

        return new IngestHl7LogWorkflowOutput();
    }

    private static ParsedLogInput parseInput(IngestHl7LogWorkflowInput input) {

        // Need either log paths or logs root path
        boolean hasLogPaths = input.logPaths() != null && !input.logPaths().isBlank();
        boolean hasLogsRootPath = input.logsRootPath() != null && !input.logsRootPath().isBlank();

        // We can accept relative log paths, but only if we have a logs root path
        List<String> logPaths = new ArrayList<>();
        List<String> relativeLogPathsWithoutRoot = new ArrayList<>();
        Path logsRootPath = hasLogsRootPath ? Path.of(input.logsRootPath()) : null;
        if (input.logPaths() != null && !input.logPaths().isBlank()) {
            for (String logPath : input.logPaths().split(",")) {
                if (logPath.startsWith("/")) {
                    logPaths.add(logPath);
                } else if (hasLogsRootPath) {
                    logPaths.add(logsRootPath.resolve(logPath).toString());
                } else {
                    relativeLogPathsWithoutRoot.add(logPath);
                }
            }
        }

        // If we do not have any log paths, we will ingest logs from "yesterday"
        //   which we define as the day before the scheduled time in the local timezone
        // Note that we only do this for scheduled runs, so we need to know the scheduled time.
        String yesterday = null;
        boolean missingScheduledTime = false;
        if (!hasLogPaths) {
            // There isn't a good API to find the scheduled start time in the SDK. We have to use a
            //  search attribute.
            // See https://docs.temporal.io/workflows#action for docs on the search attribute.
            // See also https://github.com/temporalio/features/issues/243 where someone asks
            //  for a better API for this in the SDK.
            OffsetDateTime scheduledTimeUtc = Workflow.getTypedSearchAttributes().get(SCHEDULED_START_TIME);
            if (scheduledTimeUtc == null) {
                missingScheduledTime = true;
            } else {
                ZoneId localTz = ZoneOffset.systemDefault();
                OffsetDateTime scheduledTimeLocal = scheduledTimeUtc.atZoneSameInstant(localTz).toOffsetDateTime();
                OffsetDateTime yesterdayDt = scheduledTimeLocal.minusDays(1);
                yesterday = yesterdayDt.format(YYYYMMDD_FORMAT);
                logger.info("Using date {} from scheduled workflow start time {} ({} in TZ {}) minus one day", yesterday, scheduledTimeUtc, scheduledTimeLocal, localTz);
            }
        }

        // Now we have enough information to throw for invalid inputs
        throwOnInvalidInput(input, hasLogPaths, hasLogsRootPath, relativeLogPathsWithoutRoot, missingScheduledTime);

        return new ParsedLogInput(logPaths, yesterday);
    }

    private static void throwOnInvalidInput(IngestHl7LogWorkflowInput input, boolean hasLogPaths, boolean hasLogRootPath, List<String> relativeLogPaths, boolean missingScheduledStartTime) {
        List<String> messages = new ArrayList<>();

        // Always required
        Map<String, String> requiredInputs = Map.of(
                "scratchSpaceRootPath", input.scratchSpaceRootPath(),
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

        // Missing scheduled start time
        if (missingScheduledStartTime) {
            messages.add("Can only run without logPaths for scheduled runs. Scheduled start time not found in search attributes");
        }

        if (!messages.isEmpty()) {
            throw ApplicationFailure.newNonRetryableFailure(String.join("; ", messages), "type");
        }
    }
}
