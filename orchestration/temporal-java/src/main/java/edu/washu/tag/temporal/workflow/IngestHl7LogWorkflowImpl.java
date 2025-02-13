package edu.washu.tag.temporal.workflow;

import edu.washu.tag.temporal.activity.SplitHl7LogActivity;
import edu.washu.tag.temporal.model.FindHl7LogFileInput;
import edu.washu.tag.temporal.model.FindHl7LogFileOutput;
import edu.washu.tag.temporal.model.IngestHl7FilesToDeltaLakeInput;
import edu.washu.tag.temporal.model.IngestHl7FilesToDeltaLakeOutput;
import edu.washu.tag.temporal.model.IngestHl7LogWorkflowInput;
import edu.washu.tag.temporal.model.IngestHl7LogWorkflowOutput;
import edu.washu.tag.temporal.model.SplitHl7LogActivityInput;
import edu.washu.tag.temporal.model.SplitHl7LogActivityOutput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogInput;
import edu.washu.tag.temporal.model.TransformSplitHl7LogOutput;
import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.context.Scope;
import io.temporal.activity.Activity;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.common.SearchAttributeKey;
import io.temporal.failure.ApplicationFailure;
import io.temporal.spring.boot.WorkflowImpl;
import io.temporal.workflow.ActivityStub;
import io.temporal.workflow.Async;
import io.temporal.workflow.Promise;
import io.temporal.workflow.Workflow;
import io.temporal.workflow.WorkflowInfo;
import org.slf4j.Logger;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.actuate.autoconfigure.wavefront.WavefrontProperties;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;

@Component
@WorkflowImpl(taskQueues = "ingest-hl7-log")
public class IngestHl7LogWorkflowImpl implements IngestHl7LogWorkflow {
    private static final Logger logger = Workflow.getLogger(IngestHl7LogWorkflowImpl.class);

    private static final SearchAttributeKey<OffsetDateTime> SCHEDULED_START_TIME =
            SearchAttributeKey.forOffsetDateTime("TemporalScheduledStartTime");
    private static final DateTimeFormatter YYYYMMDD_FORMAT = DateTimeFormatter.ofPattern("yyyyMMdd");

    private final SplitHl7LogActivity hl7LogActivity =
            Workflow.newActivityStub(SplitHl7LogActivity.class,
                    ActivityOptions.newBuilder()
                            .setStartToCloseTimeout(Duration.ofSeconds(10))
                            .build());

    private static final String INGEST_ACTIVITY_NAME = "ingest_hl7_files_to_delta_lake_activity";
    private final ActivityStub ingestActivity =
        Workflow.newUntypedActivityStub(
            ActivityOptions.newBuilder()
                    .setTaskQueue("ingest-hl7-delta-lake")
                    .setStartToCloseTimeout(Duration.ofSeconds(30))
                    .setRetryOptions(RetryOptions.newBuilder()
                            .setMaximumAttempts(5)
                            .build())
                    .build());

    private final Tracer tracer;

    @Autowired
    public IngestHl7LogWorkflowImpl(OpenTelemetry openTelemetry) {
        tracer = openTelemetry.getTracer("ingest-hl7-log-workflow");
    }

    @Override
    public IngestHl7LogWorkflowOutput ingestHl7Log(IngestHl7LogWorkflowInput input) {
        Span span = tracer.spanBuilder("ingestHl7Log").startSpan();
        span.setAttribute("workflowId", Workflow.getInfo().getWorkflowId());
        try (Scope scope = span.makeCurrent()) {
            WorkflowInfo workflowInfo = Workflow.getInfo();
            logger.info("Beginning workflow {} workflowId {}", this.getClass().getSimpleName(), workflowInfo.getWorkflowId());
            span.addEvent("workflow.start");

            // Log input values
            logger.debug("Input: {}", input);

            // Determine date
            String date = determineDate(input.date());

            // Validate input
            throwOnInvalidInput(input, date);

            String scratchDir = input.scratchSpaceRootPath() + (input.scratchSpaceRootPath().endsWith("/") ? "" : "/") + workflowInfo.getWorkflowId();

            // Find log file by date
            FindHl7LogFileInput findHl7LogFileInput = new FindHl7LogFileInput(date, input.logsRootPath());
            FindHl7LogFileOutput findHl7LogFileOutput = hl7LogActivity.findHl7LogFile(findHl7LogFileInput);

            // Split log file
            String splitLogFileOutputPath = scratchDir + "/split";
            SplitHl7LogActivityInput splitHl7LogInput = new SplitHl7LogActivityInput(findHl7LogFileOutput.logFileAbsPath(), splitLogFileOutputPath);
            SplitHl7LogActivityOutput splitHl7LogOutput = hl7LogActivity.splitHl7Log(splitHl7LogInput);

            // Fan out
            String hl7RootPath = input.hl7OutputPath().endsWith("/") ? input.hl7OutputPath().substring(0, input.hl7OutputPath().length() - 1) : input.hl7OutputPath();
            List<Promise<TransformSplitHl7LogOutput>> transformSplitHl7LogOutputPromises = new ArrayList<>();
            for (String splitLogFileRelativePath : splitHl7LogOutput.relativePaths()) {
                // Async call to transform split log file into HL7
                String splitLogFilePath = splitHl7LogOutput.rootPath() + "/" + splitLogFileRelativePath;
                TransformSplitHl7LogInput transformSplitHl7LogInput = new TransformSplitHl7LogInput(splitLogFilePath, hl7RootPath);
                Promise<TransformSplitHl7LogOutput> transformSplitHl7LogOutputPromise =
                        Async.function(hl7LogActivity::transformSplitHl7Log, transformSplitHl7LogInput);
                transformSplitHl7LogOutputPromises.add(transformSplitHl7LogOutputPromise);
            }
            // Collect async results
            final List<String> hl7AbsolutePaths = transformSplitHl7LogOutputPromises.stream()
                                                                                    .map(Promise::get)
                                                                                    .map(TransformSplitHl7LogOutput::relativePath)
                                                                                    .map(relativePath -> hl7RootPath + "/" + relativePath)
                                                                                    .toList();

            // Ingest HL7 into delta lake
            // We execute the activity using the untyped stub because the activity is implemented in a different language
            IngestHl7FilesToDeltaLakeOutput ingestHl7LogWorkflowOutput = ingestActivity.execute(
                    INGEST_ACTIVITY_NAME,
                    IngestHl7FilesToDeltaLakeOutput.class,
                    new IngestHl7FilesToDeltaLakeInput(input.deltaLakePath(), hl7AbsolutePaths)
            );
        } finally {
            span.addEvent("workflow.end");
            span.end();
        }

        return new IngestHl7LogWorkflowOutput();
    }

    private static void throwOnInvalidInput(IngestHl7LogWorkflowInput input, String date) {
        boolean hasLogsRootPath = input.logsRootPath() != null && !input.logsRootPath().isBlank();
        boolean hasScratchSpaceRootPath = input.scratchSpaceRootPath() != null && !input.scratchSpaceRootPath().isBlank();
        boolean hasHl7OutputPath = input.hl7OutputPath() != null && !input.hl7OutputPath().isBlank();
        boolean hasDeltaLakePath = input.deltaLakePath() != null && !input.deltaLakePath().isBlank();
        boolean hasDate = date != null;

        if (!(hasLogsRootPath && hasScratchSpaceRootPath && hasHl7OutputPath && hasDeltaLakePath && hasDate)) {
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
            if (!hasDate) {
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
    private static String determineDate(String dateInput) {
        String date;
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
                date = null;
            } else {
                // Ingest logs from "yesterday" which we define as the day before the scheduled time in the local timezone
                ZoneId localTz = ZoneOffset.systemDefault();
                OffsetDateTime scheduledTimeLocal = scheduledTimeUtc.atZoneSameInstant(localTz).toOffsetDateTime();
                OffsetDateTime yesterday = scheduledTimeLocal.minusDays(1);
                date = yesterday.format(YYYYMMDD_FORMAT);
                logger.debug("Using date {} from scheduled workflow start time {} ({} in TZ {}) minus one day", date, scheduledTimeUtc, scheduledTimeLocal, localTz);
            }
        } else {
            // Use the input date, removing any hyphens
            date = dateInput.replace("-", "");
            logger.debug("Using date {} from input value {}", date, dateInput);
        }
        return date;
    }
}
