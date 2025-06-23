package edu.washu.tag.extractor.hl7log.util;

import static edu.washu.tag.extractor.hl7log.util.Constants.SCHEDULED_START_TIME_SEARCH_ATTRIBUTE_KEY;
import static edu.washu.tag.extractor.hl7log.util.Constants.YYYYMMDD_FORMAT;

import edu.washu.tag.extractor.hl7log.model.IngestHl7LogWorkflowInput;
import edu.washu.tag.extractor.hl7log.model.IngestHl7LogWorkflowParsedInput;
import io.temporal.failure.ApplicationFailure;
import io.temporal.workflow.Workflow;
import io.temporal.workflow.WorkflowInfo;
import java.nio.file.Path;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * IngestHl7LogWorkflowInputParser is responsible for parsing and validating the input for the HL7 log ingestion workflow.
 * It handles both scheduled and non-scheduled runs, resolving paths and ensuring all required inputs are provided.
 */
public class IngestHl7LogWorkflowInputParser {

    private static final Logger logger = LoggerFactory.getLogger(IngestHl7LogWorkflowInputParser.class);

    /**
     * Parse and validate input.
     *
     * <p>Ways this workflow can be invoked:
     * 1. In a scheduled run, we ignore the other args and use the scheduled time to find "yesterday's" logs.
     *    These logs must be found under the logsRootPath (either provided in the args or the default), and they
     *    must have a date in the file name in the format YYYYMMDD.
     * 2. In a non-scheduled run we must find logs using the other args. We first assemble a list of paths to check for logs.
     *  2a. We have a logPaths input value. The paths can be absolute (used as-is) or relative (resolved against logsRootPath).
     *  2b. With no logPaths input, we will use logsRootPath to find all log files under that path.
     *  Regardless of the source of the paths, we will check each path for log files. If a path is a directory we search
     *  it recursively for files ending in ".log". If a path is a file we use it as-is.
     *  If a date arg is provided, we will only look for logs with that date in the file name.
     *
     * @param input Workflow input
     * @return Resolved inputs against defaults and paths made absolute
     */
    public static IngestHl7LogWorkflowParsedInput parseInput(IngestHl7LogWorkflowInput input) {
        WorkflowInfo workflowInfo = Workflow.getInfo();

        // Either input values or defaults
        String logsRootPath = DefaultArgs.getLogsRootPath(input.logsRootPath());
        String scratchSpaceRootPath = DefaultArgs.getScratchSpaceRootPath(input.scratchSpaceRootPath());
        String hl7OutputPath = DefaultArgs.getHl7OutputPath(input.hl7OutputPath());

        Integer splitAndUploadTimeout = DefaultArgs.getSplitAndUploadTimeout(input.splitAndUploadTimeout());
        Integer splitAndUploadHeartbeatTimeout = DefaultArgs.getSplitAndUploadHeartbeatTimeout(input.splitAndUploadHeartbeatTimeout());
        Integer splitAndUploadConcurrency = DefaultArgs.getSplitAndUploadConcurrency(input.splitAndUploadConcurrency());

        // Do we have values?
        boolean hasLogPathsInput = input.logPaths() != null && !input.logPaths().isBlank();
        boolean hasLogsRootPathInput = logsRootPath != null && !logsRootPath.isBlank();

        // Get the scheduled time
        // If we are in a scheduled run we will ingest logs from "yesterday"
        //   which we define as the day before the scheduled time in the local timezone
        // Note: There isn't a good API to find the scheduled start time in the SDK. We have to use a
        //  search attribute.
        // See https://docs.temporal.io/workflows#action for docs on the search attribute.
        // See also https://github.com/temporalio/features/issues/243 where someone asks
        //  for a better API for this in the SDK.
        OffsetDateTime scheduledTimeUtc = Workflow.getTypedSearchAttributes().get(SCHEDULED_START_TIME_SEARCH_ATTRIBUTE_KEY);
        boolean isScheduledRun = scheduledTimeUtc != null;
        if (isScheduledRun && hasLogsRootPathInput) {
            // We are in a scheduled run. Find "yesterday's" logs.
            ZoneId localTz = ZoneOffset.systemDefault();
            OffsetDateTime scheduledTimeLocal = scheduledTimeUtc.atZoneSameInstant(localTz).toOffsetDateTime();
            OffsetDateTime yesterdayDt = scheduledTimeLocal.minusDays(1);
            String date = yesterdayDt.format(YYYYMMDD_FORMAT);
            logger.info(
                "WorkflowId {} - Using date {} from scheduled workflow start time {} ({} in TZ {}) minus one day",
                workflowInfo.getWorkflowId(), date, scheduledTimeUtc, scheduledTimeLocal, localTz
            );
            return new IngestHl7LogWorkflowParsedInput(
                List.of(logsRootPath),
                date,
                scratchSpaceRootPath,
                logsRootPath,
                hl7OutputPath,
                splitAndUploadTimeout,
                splitAndUploadHeartbeatTimeout,
                splitAndUploadConcurrency
            );
        } else if (isScheduledRun) {
            // We are in a scheduled run without a root path. This is an error.
            logger.error(
                "WorkflowId {} - Scheduled run with no logsRootPath. Cannot find logs to process.",
                workflowInfo.getWorkflowId()
            );
            throw ApplicationFailure.newNonRetryableFailure("Scheduled run with no logsRootPath.", "type");
        }

        // We are not in a scheduled run. Use the other args to figure out log paths to check.
        List<String> logPaths = Collections.emptyList();
        List<String> relativeLogPathsWithoutRoot = new ArrayList<>();
        if (hasLogPathsInput) {
            // Explicit log path input takes precedence
            // We will use the log paths as-is if they are absolute, or resolve them against the logs root path
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
            // If we have a logs root path, we will search there for logs
            logPaths = List.of(logsRootPath);
        }

        // Now we have enough information to throw for invalid inputs
        throwOnInvalidInput(
            input, scratchSpaceRootPath, hl7OutputPath, hasLogPathsInput, hasLogsRootPathInput, relativeLogPathsWithoutRoot
        );

        return new IngestHl7LogWorkflowParsedInput(
            logPaths,
            input.date(),
            scratchSpaceRootPath,
            logsRootPath,
            hl7OutputPath,
            splitAndUploadTimeout,
            splitAndUploadHeartbeatTimeout,
            splitAndUploadConcurrency
        );
    }

    private static void throwOnInvalidInput(
        IngestHl7LogWorkflowInput input,
        String scratchSpaceRootPath,
        String hl7OutputPath,
        boolean hasLogPaths,
        boolean hasLogRootPath,
        List<String> relativeLogPaths
    ) {
        List<String> messages = new ArrayList<>();

        // Always required
        Map<String, Optional<String>> requiredInputs = Map.of(
            "scratchSpacePath", Optional.ofNullable(scratchSpaceRootPath),
            "hl7OutputPath", Optional.ofNullable(hl7OutputPath)
        );
        for (Map.Entry<String, Optional<String>> entry : requiredInputs.entrySet()) {
            Optional<String> value = entry.getValue();
            if (value.isEmpty() || value.get().isBlank()) {
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

        if (!messages.isEmpty()) {
            throw ApplicationFailure.newNonRetryableFailure(String.join("; ", messages), "type");
        }
    }
}
