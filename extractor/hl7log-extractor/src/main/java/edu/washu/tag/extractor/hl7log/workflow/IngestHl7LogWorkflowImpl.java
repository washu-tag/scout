package edu.washu.tag.extractor.hl7log.workflow;

import edu.washu.tag.extractor.hl7log.activity.FindHl7LogsActivity;
import edu.washu.tag.extractor.hl7log.model.ContinueIngestWorkflow;
import edu.washu.tag.extractor.hl7log.activity.SplitHl7LogActivity;
import edu.washu.tag.extractor.hl7log.model.FindHl7LogFileInput;
import edu.washu.tag.extractor.hl7log.model.FindHl7LogFileOutput;
import edu.washu.tag.extractor.hl7log.model.Hl7ManifestFileInput;
import edu.washu.tag.extractor.hl7log.model.Hl7ManifestFileOutput;
import edu.washu.tag.extractor.hl7log.model.IngestHl7FilesToDeltaLakeInput;
import edu.washu.tag.extractor.hl7log.model.IngestHl7LogWorkflowInput;
import edu.washu.tag.extractor.hl7log.model.IngestHl7LogWorkflowOutput;
import edu.washu.tag.extractor.hl7log.model.SplitAndTransformHl7LogInput;
import edu.washu.tag.extractor.hl7log.model.SplitAndTransformHl7LogOutput;
import edu.washu.tag.extractor.hl7log.util.AllOfPromiseOnlySuccesses;
import edu.washu.tag.extractor.hl7log.util.DefaultArgs;
import io.temporal.activity.ActivityOptions;
import io.temporal.api.common.v1.WorkflowExecution;
import io.temporal.api.enums.v1.ParentClosePolicy;
import io.temporal.common.RetryOptions;
import io.temporal.common.SearchAttributeKey;
import io.temporal.failure.ApplicationFailure;
import io.temporal.spring.boot.WorkflowImpl;
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

import static edu.washu.tag.extractor.hl7log.util.Constants.PARENT_QUEUE;
import static edu.washu.tag.extractor.hl7log.util.Constants.CHILD_QUEUE;
import static edu.washu.tag.extractor.hl7log.util.Constants.INGEST_DELTA_LAKE_QUEUE;

@WorkflowImpl(taskQueues = PARENT_QUEUE)
public class IngestHl7LogWorkflowImpl implements IngestHl7LogWorkflow {
    private record ParsedLogInput(
        List<String> logPaths,
        String yesterday,
        String scratchSpaceRootPath,
        String hl7OutputPath
    ) {}

    private static final Logger logger = Workflow.getLogger(IngestHl7LogWorkflowImpl.class);

    private static final SearchAttributeKey<OffsetDateTime> SCHEDULED_START_TIME =
            SearchAttributeKey.forOffsetDateTime("TemporalScheduledStartTime");
    private static final DateTimeFormatter YYYYMMDD_FORMAT = DateTimeFormatter.ofPattern("yyyyMMdd");

    private final FindHl7LogsActivity findHl7LogsActivity =
            Workflow.newActivityStub(FindHl7LogsActivity.class,
                    ActivityOptions.newBuilder()
                            .setStartToCloseTimeout(Duration.ofMinutes(5))
                            .setRetryOptions(RetryOptions.newBuilder()
                                    .setMaximumInterval(Duration.ofSeconds(1))
                                    .setMaximumAttempts(3)
                                    .build())
                            .build());

    private final IngestHl7ToDeltaLakeWorkflow ingestToDeltaLake =
        Workflow.newChildWorkflowStub(
            IngestHl7ToDeltaLakeWorkflow.class,
            ChildWorkflowOptions.newBuilder()
                .setTaskQueue(INGEST_DELTA_LAKE_QUEUE)
                .setParentClosePolicy(ParentClosePolicy.PARENT_CLOSE_POLICY_ABANDON)
                .build()
        );

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

    @Override
    public IngestHl7LogWorkflowOutput ingestHl7Log(IngestHl7LogWorkflowInput input) {
        WorkflowInfo workflowInfo = Workflow.getInfo();
        logger.info("Beginning workflow {} workflowId {}", this.getClass().getSimpleName(), workflowInfo.getWorkflowId());

        // Parse / validate input
        input = input == null ? IngestHl7LogWorkflowInput.EMPTY : input;
        ParsedLogInput parsedLogInput = parseInput(input);

        String scratchSpaceRootPath = parsedLogInput.scratchSpaceRootPath();
        String scratchDir = scratchSpaceRootPath + "/" + workflowInfo.getWorkflowId();

        // Determine if we are starting a new workflow or resuming from a manifest file
        FindHl7LogFileOutput findHl7LogFileOutput;
        if (input.continued() == null) {
            logger.info("WorkflowId {} - Starting new workflow", workflowInfo.getWorkflowId());

            // Construct a path for a new manifest file
            String manifestFilePath = scratchDir + "/log-manifest.txt";

            // Get list of log paths to process
            FindHl7LogFileInput findHl7LogFileInput = new FindHl7LogFileInput(
                parsedLogInput.logPaths(), parsedLogInput.yesterday(), input.logsRootPath(), manifestFilePath
            );
            findHl7LogFileOutput = findHl7LogsActivity.findHl7LogFiles(findHl7LogFileInput);
        } else {
            logger.info("WorkflowId {} - Resuming from continued {}", workflowInfo.getWorkflowId(), input.continued());
            findHl7LogFileOutput = findHl7LogsActivity.continueIngestHl7LogWorkflow(input.continued());
        }

        // At this point we have a list of file paths to process.
        // We may have a manifest file and a next offset into that file; if so we will continue as new at the end.

        // Launch child activity for each log file
        logger.info("WorkflowId {} - Launching {} async activities", workflowInfo.getWorkflowId(), findHl7LogFileOutput.logFiles().size());
        List<Promise<SplitAndTransformHl7LogOutput>> transformSplitHl7LogOutputPromises = findHl7LogFileOutput.logFiles().stream()
                .map(logFile -> Async.function(
                        hl7LogActivity::splitAndTransformHl7Log,
                        new SplitAndTransformHl7LogInput(logFile, parsedLogInput.hl7OutputPath(), scratchSpaceRootPath)
                ))
                .toList();

        // Collect async results
        logger.info("WorkflowId {} - Waiting for {} async activities to complete", workflowInfo.getWorkflowId(), transformSplitHl7LogOutputPromises.size());
        List<SplitAndTransformHl7LogOutput> transformSplitHl7LogOutputs = new AllOfPromiseOnlySuccesses<>(transformSplitHl7LogOutputPromises).get();
        if (transformSplitHl7LogOutputs.isEmpty()) {
            throw ApplicationFailure.newNonRetryableFailure("All split and transformation activities failed", "type");
        }

        logger.info("WorkflowId {} - Collecting results for {} successful async activities", workflowInfo.getWorkflowId(), transformSplitHl7LogOutputs.size());
        // Collect HL7 file-path-file paths
        // This sounds more confusing than it is.
        // Each split and transform activity writes a single file with the paths of the HL7 files it created.
        // We collect the paths to these files (the contents of each being file paths) and pass them to the ingest activity.
        int[] numHl7FilesHolder = {0};
        List<String> hl7FilePathFiles = transformSplitHl7LogOutputs.stream()
                .peek(output -> numHl7FilesHolder[0] += output.numHl7Files())
                .map(SplitAndTransformHl7LogOutput::hl7FilesOutputFilePath)
                .toList();

        // Write manifest file
        logger.info("WorkflowId {} - Collecting {} HL7 files from {} successful async activities into manifest file",
            workflowInfo.getWorkflowId(), numHl7FilesHolder[0], transformSplitHl7LogOutputs.size());
        Hl7ManifestFileOutput hl7ManifestFileOutput = hl7LogActivity.writeHl7ManifestFile(new Hl7ManifestFileInput(hl7FilePathFiles, scratchDir));

        // Ingest HL7 into delta lake
        logger.info("WorkflowId {} - Launching workflow to ingest {} HL7 files",
                workflowInfo.getWorkflowId(), hl7ManifestFileOutput.numHl7Files());
        Async.function(
            ingestToDeltaLake::ingestHl7FileToDeltaLake,
            new IngestHl7FilesToDeltaLakeInput(
                input.deltaLakePath(),
                input.modalityMapPath(),
                scratchSpaceRootPath,
                hl7ManifestFileOutput.manifestFilePath(),
                null,
                input.reportTableName()
            )
        );
        // Wait for child workflow to start
        Promise<WorkflowExecution> childPromise = Workflow.getWorkflowExecution(ingestToDeltaLake);
        WorkflowExecution child = childPromise.get();
        logger.info("WorkflowId {} - Launched child ingest workflow {}", workflowInfo.getWorkflowId(), child.getWorkflowId());

        // If we have a non-null continuation object, we will continue as new with the next index
        ContinueIngestWorkflow nextContinued = findHl7LogFileOutput.continued();
        if (nextContinued != null) {
            logger.info("WorkflowId {} - Continuing as new workflow - nextContinued {}", workflowInfo.getWorkflowId(), nextContinued);
            Workflow.continueAsNew(
                new IngestHl7LogWorkflowInput(
                    input.date(),
                    input.logsRootPath(),
                    input.logPaths(),
                    scratchSpaceRootPath,
                    input.hl7OutputPath(),
                    input.deltaLakePath(),
                    input.modalityMapPath(),
                    input.reportTableName(),
                    nextContinued
                )
            );
        } else {
            logger.info("Completed workflow {} workflowId {}", this.getClass().getSimpleName(), workflowInfo.getWorkflowId());
        }
        return new IngestHl7LogWorkflowOutput();
    }

    /**
     * Parse and validate input.
     *
     * @param input Workflow input
     * @return Log paths and "yesterday" date, if we are in a scheduled run
     */
    private ParsedLogInput parseInput(IngestHl7LogWorkflowInput input) {
        // Default values
        String logsRootPath = DefaultArgs.getLogsRootPath(input.logsRootPath());
        String scratchSpaceRootPath = DefaultArgs.getScratchSpaceRootPath(input.scratchSpaceRootPath());
        String hl7OutputPath = DefaultArgs.getHl7OutputPath(input.hl7OutputPath());

        // Need either log paths or logs root path
        boolean hasLogPathsInput = input.logPaths() != null && !input.logPaths().isBlank();
        boolean hasLogsRootPathInput = logsRootPath != null && !logsRootPath.isBlank();
        List<String> logPaths;
        List<String> relativeLogPathsWithoutRoot = new ArrayList<>();
        if (hasLogPathsInput) {
            logPaths = new ArrayList<>();
            Path logsRoot = hasLogsRootPathInput ? Path.of(logsRootPath) : null;
            for (String logPath : input.logPaths().split(",")) {
                if (logPath.startsWith("/")) {
                    logPaths.add(logPath);
                } else if (hasLogsRootPathInput) {
                    logPaths.add(logsRoot.resolve(logPath).toString());
                } else {
                    relativeLogPathsWithoutRoot.add(logPath);
                }
            }
        } else if (hasLogsRootPathInput) {
            // If we have a logs root path, we will ingest all logs from that path
            logPaths = List.of(logsRootPath);
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
            logger.info(
                    "Using date {} from scheduled workflow start time {} ({} in TZ {}) minus one day",
                    yesterday, scheduledTimeUtc, scheduledTimeLocal, localTz
            );
        }

        // Now we have enough information to throw for invalid inputs
        throwOnInvalidInput(
                input, scratchSpaceRootPath, hl7OutputPath, hasLogPathsInput, hasLogsRootPathInput, relativeLogPathsWithoutRoot, hasScheduledTime
        );

        return new ParsedLogInput(logPaths, yesterday, scratchSpaceRootPath, hl7OutputPath);
    }

    private void throwOnInvalidInput(
            IngestHl7LogWorkflowInput input,
            String scratchSpaceRootPath,
            String hl7OutputPath,
            boolean hasLogPaths,
            boolean hasLogRootPath,
            List<String> relativeLogPaths,
            boolean hasScheduledTime
    ) {
        List<String> messages = new ArrayList<>();

        // Always required
        Map<String, String> requiredInputs = Map.of(
                "scratchSpacePath", scratchSpaceRootPath,
                "hl7OutputPath", hl7OutputPath
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
