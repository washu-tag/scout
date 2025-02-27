package edu.washu.tag.temporal.workflow;

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
import org.apache.commons.lang3.tuple.Pair;
import org.slf4j.Logger;

import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Deque;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@WorkflowImpl(taskQueues = "ingest-hl7-log")
public class IngestHl7LogWorkflowImpl implements IngestHl7LogWorkflow {
    private static final Logger logger = Workflow.getLogger(IngestHl7LogWorkflowImpl.class);

    private static final SearchAttributeKey<OffsetDateTime> SCHEDULED_START_TIME =
            SearchAttributeKey.forOffsetDateTime("TemporalScheduledStartTime");
    private static final DateTimeFormatter YYYYMMDD_FORMAT = DateTimeFormatter.ofPattern("yyyyMMdd");

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

        // Log input values
        logger.debug("Input: {}", input);

        // Determine dates
        List<String> dates = determineDate(input.date());

        // Validate input
        throwOnInvalidInput(input, dates);

        // Launch child workflow for each date
        Deque<Promise<Hl7FromHl7LogWorkflowOutput>> childWorkflowOutputs = dates.stream()
                .map(date -> Async.function(
                        hl7FromHl7LogWorkflow::splitAndTransformHl7Log,
                        new Hl7FromHl7LogWorkflowInput(date, input.logsRootPath(), input.scratchSpaceRootPath(), input.hl7OutputPath())
                ))
                .collect(Collectors.toCollection(LinkedList::new));
        List<Hl7FromHl7LogWorkflowOutput> childWorkflowResults = WorkflowUtils.getSuccessfulResults(childWorkflowOutputs, logger);
        if (childWorkflowResults.isEmpty()) {
            throw ApplicationFailure.newNonRetryableFailure("Child workflows failed", "type");
        }

        // Partition hl7 paths by year, so it can avoid race conditions on writing the files
        final Map<String, List<String>> hl7AbsolutePathsByYear = childWorkflowResults.stream()
                // Pair of year and hl7 paths
                .map(output -> Pair.of(output.date().substring(0, 4), output.hl7Paths()))
                // Flatten paths list to pair of year and single hl7 path
                .flatMap(pair -> pair.getRight().stream().map(path -> Pair.of(pair.getLeft(), path)))
                // Group by year
                .collect(
                        Collectors.groupingBy(
                                Pair::getLeft,
                                Collectors.mapping(Pair::getRight, Collectors.toList())
                        )
                );

        // Ingest HL7 into delta lake
        // We execute the activity using the untyped stub because the activity is implemented in a different language
        for (Map.Entry<String, List<String>> hl7AbsolutePathsEntry : hl7AbsolutePathsByYear.entrySet()) {
            List<String> hl7AbsolutePaths = hl7AbsolutePathsEntry.getValue();
            logger.info("Launching activity to ingest {} HL7 files for year {}",
                    hl7AbsolutePaths.size(), hl7AbsolutePathsEntry.getKey());
            IngestHl7FilesToDeltaLakeOutput ingestHl7LogWorkflowOutput = ingestActivity.execute(
                    INGEST_ACTIVITY_NAME,
                    IngestHl7FilesToDeltaLakeOutput.class,
                    new IngestHl7FilesToDeltaLakeInput(input.deltaLakePath(), input.modalityMapPath(), hl7AbsolutePaths)
            );
        }

        return new IngestHl7LogWorkflowOutput();
    }

    private static void throwOnInvalidInput(IngestHl7LogWorkflowInput input, List<String> dates) {
        boolean hasLogsRootPath = input.logsRootPath() != null && !input.logsRootPath().isBlank();
        boolean hasScratchSpaceRootPath = input.scratchSpaceRootPath() != null && !input.scratchSpaceRootPath().isBlank();
        boolean hasHl7OutputPath = input.hl7OutputPath() != null && !input.hl7OutputPath().isBlank();
        boolean hasDeltaLakePath = input.deltaLakePath() != null && !input.deltaLakePath().isBlank();
        boolean hasDates = dates != null && !dates.isEmpty();

        if (!(hasLogsRootPath && hasScratchSpaceRootPath && hasHl7OutputPath && hasDeltaLakePath && hasDates)) {
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
            if (!hasDeltaLakePath) {
                missingInputs.add("deltaLakePath");
            }
            if (!hasDates) {
                missingInputs.add("date");
            }
            String plural = missingInputs.size() == 1 ? "" : "s";
            String missingInputsStr = String.join(", ", missingInputs);
            throw ApplicationFailure.newNonRetryableFailure("Missing required input" + plural + ": " + missingInputsStr, "type");
        }
    }

    /**
     * Determine the date to use for the workflow.
     * If the input date is null, use the scheduled start time of the workflow minus one day—i.e. "yesterday".
     * @param dateInput The date value from the workflow inputs
     * @return Date to use for the workflow
     */
    private static List<String> determineDate(String dateInput) {
        List<String> dates;
        if (dateInput == null) {
            // Get the date from the time the workflow was scheduled to start
            // Note that there isn't a good API for this in the SDK. We have to use a
            //  search attribute.
            // See https://docs.temporal.io/workflows#action for docs on the search attribute.
            // See also https://github.com/temporalio/features/issues/243 where someone asks
            //  for a better API for this in the SDK.
            OffsetDateTime scheduledTimeUtc = Workflow.getTypedSearchAttributes().get(SCHEDULED_START_TIME);

            if (scheduledTimeUtc == null) {
                logger.debug("No date input, and scheduled start time not found in search attributes.");
                dates = Collections.emptyList();
            } else {
                // Ingest logs from "yesterday" which we define as the day before the scheduled time in the local timezone
                ZoneId localTz = ZoneOffset.systemDefault();
                OffsetDateTime scheduledTimeLocal = scheduledTimeUtc.atZoneSameInstant(localTz).toOffsetDateTime();
                OffsetDateTime yesterday = scheduledTimeLocal.minusDays(1);
                String date = yesterday.format(YYYYMMDD_FORMAT);
                logger.debug("Using date {} from scheduled workflow start time {} ({} in TZ {}) minus one day", date, scheduledTimeUtc, scheduledTimeLocal, localTz);
                dates = List.of(date);
            }
        } else {
            dates = Arrays.stream(dateInput.split(","))
                    .map(date -> date.replace("-", ""))
                    .toList();
            logger.debug("Using dates {} from input value {}", dates, dateInput);
        }
        return dates;
    }
}
