package edu.washu.tag.extractor.hl7log;

import static edu.washu.tag.extractor.hl7log.util.Constants.YYYYMMDD_FORMAT;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;

import edu.washu.tag.extractor.hl7log.model.IngestHl7LogWorkflowInput;
import edu.washu.tag.extractor.hl7log.model.IngestHl7LogWorkflowParsedInput;
import edu.washu.tag.extractor.hl7log.util.Constants;
import edu.washu.tag.extractor.hl7log.util.IngestHl7LogWorkflowInputParser;
import io.temporal.api.enums.v1.IndexedValueType;
import io.temporal.common.SearchAttributeUpdate;
import io.temporal.testing.TestWorkflowExtension;
import io.temporal.workflow.Workflow;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;
import java.time.Clock;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.RegisterExtension;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;

@SpringBootTest
class IngestHl7LogWorkflowInputParserTest {
    @WorkflowInterface
    public interface IngestHl7LogWorkflowInputParserTestWorkflow {
        @WorkflowMethod
        IngestHl7LogWorkflowParsedInput parseInput(IngestHl7LogWorkflowInput input, OffsetDateTime scheduledStartTime);
    }

    public static class IngestHl7LogWorkflowInputParserTestWorkflowImpl implements IngestHl7LogWorkflowInputParserTestWorkflow {
        @Override
        public IngestHl7LogWorkflowParsedInput parseInput(IngestHl7LogWorkflowInput input, OffsetDateTime scheduledStartTime) {
            if (scheduledStartTime != null) {
                // Simulate the scheduled start time
                Workflow.upsertTypedSearchAttributes(
                    SearchAttributeUpdate.valueSet(Constants.SCHEDULED_START_TIME_SEARCH_ATTRIBUTE_KEY, scheduledStartTime)
                );
            }
            return IngestHl7LogWorkflowInputParser.parseInput(input);
        }
    }

    @RegisterExtension
    public static final TestWorkflowExtension testWorkflowExtension =
        TestWorkflowExtension.newBuilder()
            .registerWorkflowImplementationTypes(IngestHl7LogWorkflowInputParserTestWorkflowImpl.class)
            .registerSearchAttribute(Constants.TEMPORAL_SCHEDULED_START_TIME, IndexedValueType.INDEXED_VALUE_TYPE_DATETIME)
            .build();

    @Value("${scout.workflowArgDefaults.ingestHl7Log.logsRootPath}")
    private String defaultLogsRootPath;
    @Value("${scout.workflowArgDefaults.ingestHl7Log.scratchSpaceRootPath}")
    private String defaultScratchSpaceRootPath;
    @Value("${scout.workflowArgDefaults.ingestHl7Log.hl7OutputPath}")
    private String defaultHl7OutputPath;
    @Value("${scout.workflowArgDefaults.ingestHl7Log.splitAndUploadTimeout}")
    private Integer defaultSplitAndUploadTimeout;
    @Value("${scout.workflowArgDefaults.ingestHl7Log.splitAndUploadConcurrency}")
    private Integer defaultSplitAndUploadConcurrency;

    @Test
    void testParseInput_nonScheduled_defaultsOnly(IngestHl7LogWorkflowInputParserTestWorkflow workflow) {
        IngestHl7LogWorkflowInput input = IngestHl7LogWorkflowInput.EMPTY;

        IngestHl7LogWorkflowParsedInput parsedInput = workflow.parseInput(input, null);

        assertNotNull(parsedInput);
        assertEquals(List.of(defaultLogsRootPath), parsedInput.logPaths());
        assertNull(parsedInput.date());
        assertEquals(defaultScratchSpaceRootPath, parsedInput.scratchSpaceRootPath());
        assertEquals(defaultLogsRootPath, parsedInput.logsRootPath());
        assertEquals(defaultHl7OutputPath, parsedInput.hl7OutputPath());
        assertEquals(defaultSplitAndUploadTimeout, parsedInput.splitAndUploadTimeout());
        assertEquals(defaultSplitAndUploadConcurrency, parsedInput.splitAndUploadConcurrency());
    }

    @Test
    void testParseInput_nonScheduled_logPaths(IngestHl7LogWorkflowInputParserTestWorkflow workflow) {
        String logsRootPath = "/nondefault/path/to/logs";
        List<String> logPathsInput = List.of("/absolute", "relative");
        List<String> expectedLogPaths = List.of("/absolute", logsRootPath + "/relative");
        IngestHl7LogWorkflowInput input = new IngestHl7LogWorkflowInput(
            null,
            logsRootPath,
            String.join(",", logPathsInput),
            null,
            null,
            null,
            null,
            null,
            null,
            null,
            null
        );

        IngestHl7LogWorkflowParsedInput parsedInput = workflow.parseInput(input, null);

        assertNotNull(parsedInput);
        assertEquals(expectedLogPaths, parsedInput.logPaths());
        assertNull(parsedInput.date());
        assertEquals(defaultScratchSpaceRootPath, parsedInput.scratchSpaceRootPath());
        assertEquals(logsRootPath, parsedInput.logsRootPath());
        assertEquals(defaultHl7OutputPath, parsedInput.hl7OutputPath());
        assertEquals(defaultSplitAndUploadTimeout, parsedInput.splitAndUploadTimeout());
        assertEquals(defaultSplitAndUploadConcurrency, parsedInput.splitAndUploadConcurrency());
    }

    @Test
    void testParseInput_nonScheduled_date(IngestHl7LogWorkflowInputParserTestWorkflow workflow) {
        String date = "arbitrary-date";
        IngestHl7LogWorkflowInput input = new IngestHl7LogWorkflowInput(
            date,
            null,
            null,
            null,
            null,
            null,
            null,
            null,
            null,
            null,
            null
        );

        IngestHl7LogWorkflowParsedInput parsedInput = workflow.parseInput(input, null);

        assertNotNull(parsedInput);
        assertEquals(List.of(defaultLogsRootPath), parsedInput.logPaths());
        assertEquals(date, parsedInput.date());
        assertEquals(defaultScratchSpaceRootPath, parsedInput.scratchSpaceRootPath());
        assertEquals(defaultLogsRootPath, parsedInput.logsRootPath());
        assertEquals(defaultHl7OutputPath, parsedInput.hl7OutputPath());
        assertEquals(defaultSplitAndUploadTimeout, parsedInput.splitAndUploadTimeout());
        assertEquals(defaultSplitAndUploadConcurrency, parsedInput.splitAndUploadConcurrency());
    }

    @Test
    void testParseInput_scheduled_defaultsOnly(IngestHl7LogWorkflowInputParserTestWorkflow workflow) {
        // Set search attribute to simulate a scheduled run
        OffsetDateTime nowUtc = OffsetDateTime.now(Clock.systemUTC());
        String yesterdayLocal = nowUtc
            .atZoneSameInstant(ZoneId.systemDefault())
            .toOffsetDateTime()
            .minusDays(1)
            .format(YYYYMMDD_FORMAT);

        IngestHl7LogWorkflowInput input = IngestHl7LogWorkflowInput.EMPTY;

        IngestHl7LogWorkflowParsedInput parsedInput = workflow.parseInput(input, nowUtc);

        assertNotNull(parsedInput);
        assertEquals(List.of(defaultLogsRootPath), parsedInput.logPaths());
        assertEquals(yesterdayLocal, parsedInput.date());
        assertEquals(defaultScratchSpaceRootPath, parsedInput.scratchSpaceRootPath());
        assertEquals(defaultLogsRootPath, parsedInput.logsRootPath());
        assertEquals(defaultHl7OutputPath, parsedInput.hl7OutputPath());
        assertEquals(defaultSplitAndUploadTimeout, parsedInput.splitAndUploadTimeout());
        assertEquals(defaultSplitAndUploadConcurrency, parsedInput.splitAndUploadConcurrency());
    }

    @Test
    void testParseInput_scheduled_logsRootPath(IngestHl7LogWorkflowInputParserTestWorkflow workflow) {
        String logsRootPath = "/nondefault/path/to/logs";
        IngestHl7LogWorkflowInput input = new IngestHl7LogWorkflowInput(
            null,
            logsRootPath,
            null,
            null,
            null,
            null,
            null,
            null,
            null,
            null,
            null
        );

        // Set search attribute to simulate a scheduled run
        OffsetDateTime nowUtc = OffsetDateTime.now(Clock.systemUTC());
        String yesterdayLocal = nowUtc
            .atZoneSameInstant(ZoneId.systemDefault())
            .toOffsetDateTime()
            .minusDays(1)
            .format(YYYYMMDD_FORMAT);

        IngestHl7LogWorkflowParsedInput parsedInput = workflow.parseInput(input, nowUtc);

        assertNotNull(parsedInput);
        assertEquals(List.of(logsRootPath), parsedInput.logPaths());
        assertEquals(yesterdayLocal, parsedInput.date());
        assertEquals(defaultScratchSpaceRootPath, parsedInput.scratchSpaceRootPath());
        assertEquals(logsRootPath, parsedInput.logsRootPath());
        assertEquals(defaultHl7OutputPath, parsedInput.hl7OutputPath());
        assertEquals(defaultSplitAndUploadTimeout, parsedInput.splitAndUploadTimeout());
        assertEquals(defaultSplitAndUploadConcurrency, parsedInput.splitAndUploadConcurrency());
    }

    @Test
    void testParseInput_scheduled_ignoresOtherInputs(IngestHl7LogWorkflowInputParserTestWorkflow workflow) {
        String date = "arbitrary-date";
        String logsRootPath = "/nondefault/path/to/logs";
        List<String> ignoredLogPaths = List.of("/absolute", "relative");

        IngestHl7LogWorkflowInput input = new IngestHl7LogWorkflowInput(
            date,
            logsRootPath,
            String.join(",", ignoredLogPaths),
            null,
            null,
            null,
            null,
            null,
            null,
            null,
            null
        );

        // Set search attribute to simulate a scheduled run
        OffsetDateTime nowUtc = OffsetDateTime.now(Clock.systemUTC());
        String yesterdayLocal = nowUtc
            .atZoneSameInstant(ZoneId.systemDefault())
            .toOffsetDateTime()
            .minusDays(1)
            .format(YYYYMMDD_FORMAT);

        IngestHl7LogWorkflowParsedInput parsedInput = workflow.parseInput(input, nowUtc);

        assertNotNull(parsedInput);
        assertEquals(List.of(logsRootPath), parsedInput.logPaths());
        assertEquals(yesterdayLocal, parsedInput.date());
        assertEquals(defaultScratchSpaceRootPath, parsedInput.scratchSpaceRootPath());
        assertEquals(logsRootPath, parsedInput.logsRootPath());
        assertEquals(defaultHl7OutputPath, parsedInput.hl7OutputPath());
        assertEquals(defaultSplitAndUploadTimeout, parsedInput.splitAndUploadTimeout());
        assertEquals(defaultSplitAndUploadConcurrency, parsedInput.splitAndUploadConcurrency());
    }

    @Test
    void testParseInput_splitAndUploadActivitySettings(IngestHl7LogWorkflowInputParserTestWorkflow workflow) {
        String date = "arbitrary-date";
        int timeout = 60;
        int concurrency = 100;
        IngestHl7LogWorkflowInput input = new IngestHl7LogWorkflowInput(
            date,
            null,
            null,
            null,
            null,
            timeout,
            concurrency,
            null,
            null,
            null,
            null
        );

        IngestHl7LogWorkflowParsedInput parsedInput = workflow.parseInput(input, null);

        assertNotNull(parsedInput);
        assertEquals(List.of(defaultLogsRootPath), parsedInput.logPaths());
        assertEquals(date, parsedInput.date());
        assertEquals(defaultScratchSpaceRootPath, parsedInput.scratchSpaceRootPath());
        assertEquals(defaultLogsRootPath, parsedInput.logsRootPath());
        assertEquals(defaultHl7OutputPath, parsedInput.hl7OutputPath());
        assertEquals(timeout, parsedInput.splitAndUploadTimeout());
        assertEquals(concurrency, parsedInput.splitAndUploadConcurrency());
    }
}