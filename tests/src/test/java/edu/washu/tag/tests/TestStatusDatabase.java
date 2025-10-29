package edu.washu.tag.tests;

import static org.assertj.core.api.AssertionsForClassTypes.assertThat;

import edu.washu.tag.BaseTest;
import edu.washu.tag.model.IngestJobDetails;
import edu.washu.tag.model.IngestJobInput;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.Collections;
import java.util.List;
import java.util.function.Consumer;
import java.util.stream.Collectors;
import java.util.stream.IntStream;
import java.util.stream.Stream;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

public class TestStatusDatabase extends BaseTest {

    private static final Logger logger = LoggerFactory.getLogger(TestStatusDatabase.class);
    private static final String TABLE_FILE_STATUSES = "file_statuses";
    private static final String TABLE_HL7_FILES = "hl7_files";
    private static final String VIEW_RECENT_HL7_FILES = "recent_hl7_files";

    private static final String FILE_PATH = "file_path";
    private static final String LOG_FILE_PATH = "log_file_path";
    private static final String HL7_FILE_PATH = "hl7_file_path";
    private static final String MESSAGE_NUMBER = "message_number";
    private static final String DATE = "date";

    private static final String PARSED = "parsed";
    private static final String STAGED = "staged";
    private static final String SUCCESS = "success";
    private static final String FAILED = "failed";
    private static final String LOG_TYPE = "Log";
    private static final String HL7_TYPE = "HL7";
    public static final String STATUS = "status";
    public static final String TYPE = "type";
    public static final String ERROR_MESSAGE = "error_message";

    public static final int SPLIT_AND_UPLOAD_RETRIES = 5;

    private static final String TABLE = "postgres" + System.currentTimeMillis();
    protected IngestJobDetails ingestWorkflow;

    @BeforeClass
    private void launchIngestion() {
        ingestWorkflow = temporalClient.launchIngest(
            new IngestJobInput().setReportTableName(TABLE).setLogsRootPath("/data/postgres"),
            true
        );
    }

    /**
     * Tests the state of the ingest database after the test data has been processed by Scout.
     * In particular, for the date 1995-04-02, there should be 2 HL7 messages represented by the log file.
     * The {@value #TABLE_FILE_STATUSES} table should have
     * a single successful row with a `file_path` corresponding to that date.
     * The {@value #TABLE_HL7_FILES} table should contain one "staged" row and one "success" row per file.
     * The {@value #VIEW_RECENT_HL7_FILES} view should contain only the two "success" rows, one per file.
     */
    @Test
    public void testStatusDbSuccess() {
        final List<String> parentWorkflowId = Collections.singletonList(ingestWorkflow.getIngestWorkflowId());
        final List<String> ingestWorkflowIds = Stream.concat(
            parentWorkflowId.stream(),
            ingestWorkflow.getIngestToDeltaLakeWorkflows().stream()
        ).toList();
        final String date = "19950402";

        final FileStatus logRow = FileStatus.parsedLog();
        runLogStatusTest(FileStatusLogQuery.tableQuery(date, parentWorkflowId), logRow);

        final Hl7FilesRow h0 = new Hl7FilesRow("1995/04/02/07/199504020707509258_0.hl7", 0, date);
        final Hl7FilesRow h1 = new Hl7FilesRow("1995/04/02/09/199504020930172230_1.hl7", 1, date);
        final FileStatus h0Staged = FileStatus.staged(h0.hl7FilePath);
        final FileStatus h0Success = FileStatus.success(h0.hl7FilePath);
        final FileStatus h1Staged = FileStatus.staged(h1.hl7FilePath);
        final FileStatus h1Success = FileStatus.success(h1.hl7FilePath);

        runHl7FilesTest(
            new Hl7FileTableQuery(date),
            h0,
            h1
        );

        runHl7FileStatusTest(
            FileStatusHl7Query.tableQuery(date, ingestWorkflowIds),
            h0Staged,
            h1Staged,
            h0Success,
            h1Success
        );

        runHl7FilesTest(
            new Hl7FileTableQuery(date),
            h0,
            h1
        );
        runRecentHl7FilesTest(
            new RecentHl7FilesViewQuery(date, ingestWorkflowIds),
            new RecentHl7FileRow(h0, h0Success),
            new RecentHl7FileRow(h1, h1Success)
        );
    }

    /**
     * Tests the state of the ingest database after the test data has been processed by Scout.
     * In particular, for the date 2024-01-02, the entire content of the log file is unusable by Scout.
     * The {@value #TABLE_FILE_STATUSES} table is expected to have multiple rows for that day:
     * one "{@value #PARSED}" row and one "{@value #FAILED}" row for each of the retries.
     * The {@value #TABLE_HL7_FILES} and {@value #VIEW_RECENT_HL7_FILES} should not contain
     * any rows for that day.
     */
    @Test
    public void testStatusDbTotalFailure() {
        final List<String> workflows = Collections.singletonList(ingestWorkflow.getIngestWorkflowId());
        final String date = "20240102";

        final FileStatus logRow0 = FileStatus.parsedLog();
        final FileStatus logRow1 = FileStatus.failedLog("Log did not contain any HL7 messages");
        runLogStatusTest(
            FileStatusLogQuery.tableQuery(date, workflows),
            IntStream.range(0, SPLIT_AND_UPLOAD_RETRIES)
                .flatMap(i -> IntStream.of(0, 1))
                .mapToObj(i -> i == 0 ? logRow0 : logRow1)
                .toArray(FileStatus[]::new)
        );

        runHl7FilesTest(new Hl7FileTableQuery(date));
        runHl7FileStatusTest(FileStatusHl7Query.tableQuery(date, workflows));

        runRecentHl7FilesTest(new RecentHl7FilesViewQuery(date, workflows));
    }

    /**
     * Tests the state of the ingest database after the test data has been processed by Scout.
     * In particular, for the date 2023-01-13, the log file contains an unusable HL7 message
     * followed by 2 usable ones.
     * The {@value #TABLE_FILE_STATUSES} table should have a single
     * successful row for that log file.
     * The {@value #TABLE_FILE_STATUSES} table contains
     *  1. a "staged" row for all three HL7 messages (with the first workflow id),
     *  2. a "failed" row for the improper HL7 message (with one of the delta lake ingest child workflow ids), and
     *  3. a "success" row for the two proper HL7 messages (with the second workflow id).
     * The {@value #VIEW_RECENT_HL7_FILES} view contains 2. and 3. but not 1.
     * The {@value #TABLE_HL7_FILES} table contains entries for all three HL7 messages.
     */
    @Test
    public void testStatusDbHl7MessageParseError() {
        final String date = "20230113";
        final List<String> parentWorkflowId = Collections.singletonList(ingestWorkflow.getIngestWorkflowId());

        final FileStatus logRow = FileStatus.parsedLog();
        runLogStatusTest(
            FileStatusLogQuery.tableQuery(date, parentWorkflowId),
            logRow
        );

        final List<String> ingestWorkflowIds = Stream.concat(
            parentWorkflowId.stream(),
            ingestWorkflow.getIngestToDeltaLakeWorkflows().stream()
        ).toList();

        final Hl7FilesRow hl70 = new Hl7FilesRow("2023/01/13/12/202301131207178754_0.hl7", 0, date);
        final Hl7FilesRow hl71 = new Hl7FilesRow("2023/01/13/18/202301131807178754_1.hl7", 1, date);
        final Hl7FilesRow hl72 = new Hl7FilesRow("2023/01/13/20/202301132017309482_2.hl7", 2, date);
        final FileStatus hl70Staged = FileStatus.staged(hl70.hl7FilePath);
        final FileStatus hl71Staged = FileStatus.staged(hl71.hl7FilePath);
        final FileStatus hl72Staged = FileStatus.staged(hl72.hl7FilePath);
        final FileStatus hl70Failed = FileStatus.failedHl7(hl70.hl7FilePath, "File is not parsable as HL7");
        final FileStatus hl71Success = FileStatus.success(hl71.hl7FilePath);
        final FileStatus hl72Success = FileStatus.success(hl72.hl7FilePath);

        // All four statuses
        runHl7FileStatusTest(
            FileStatusHl7Query.tableQuery(date, ingestWorkflowIds),
            hl70Staged,
            hl71Staged,
            hl72Staged,
            hl70Failed,
            hl71Success,
            hl72Success
        );
        // Three files
        runHl7FilesTest(
            new Hl7FileTableQuery(date),
            hl70,
            hl71,
            hl72
        );
        // Three files + most recent statuses
        runRecentHl7FilesTest(
            new RecentHl7FilesViewQuery(date, ingestWorkflowIds),
            new RecentHl7FileRow(hl70, hl70Failed),
            new RecentHl7FileRow(hl71, hl71Success),
            new RecentHl7FileRow(hl72, hl72Success)
        );
    }

    /**
     * Tests the state of the ingest database after the test data has been processed by Scout.
     * In particular, for the date 2007-10-21, the log file contains an HL7 message with repeated content.
     * The {@value #TABLE_FILE_STATUSES} table should have a single
     * successful row for the log file corresponding to that date.
     * The {@value #TABLE_FILE_STATUSES} table contains one "staged" row and one "success" row for the
     * first HL7 message, and one "failed" row for the repeated message.
     * The {@value #TABLE_HL7_FILES} table contains a row for the HL7 file.
     * The {@value #VIEW_RECENT_HL7_FILES} view contains the "failed" row for the repeated message and
     * the "success" row for the first message.
     */
    @Test
    public void testStatusDbHl7MessageRepeatError() {
        final String date = "20071021";
        final List<String> parentWorkflowId = Collections.singletonList(ingestWorkflow.getIngestWorkflowId());
        final List<String> ingestWorkflowIds = Stream.concat(
            parentWorkflowId.stream(),
            ingestWorkflow.getIngestToDeltaLakeWorkflows().stream()
        ).toList();

        final FileStatus logRow = FileStatus.parsedLog();
        runLogStatusTest(
            FileStatusLogQuery.tableQuery(date, parentWorkflowId),
            logRow
        );

        final Hl7FilesRow hl70 = new Hl7FilesRow("2007/10/21/15/200710211522316785_0.hl7", 0, date);
        final Hl7FilesRow hl71 = new Hl7FilesRow(null, 1, date);
        final FileStatus hl70Staged = FileStatus.staged(hl70.hl7FilePath);
        final FileStatus hl70Success = FileStatus.success(hl70.hl7FilePath);
        final FileStatus hl71Failed = FileStatus.failedHl7(hl71.hl7FilePath,
            "HL7 content did not contain a timestamp header line; this usually means it is a repeat of the previous message's HL7 content");

        runHl7FileStatusTest(
            FileStatusHl7Query.tableQuery(date, ingestWorkflowIds),
            hl71Failed,
            hl70Staged,
            hl70Success
        );

        runHl7FilesTest(
            new Hl7FileTableQuery(date),
            hl70,
            hl71
        );
        runRecentHl7FilesTest(
            new RecentHl7FilesViewQuery(date, ingestWorkflowIds),
            new RecentHl7FileRow(hl71, hl71Failed),
            new RecentHl7FileRow(hl70, hl70Success)
        );
    }

    /**
     * Tests the state of the ingest database after the test data has been processed by Scout.
     * In particular, for the date 2019-01-06, the log file contains an HL7 message with no content.
     * The {@value #TABLE_FILE_STATUSES} table should have multiple "parsed" rows for the log file
     * corresponding to that date because of retries.
     * The {@value #TABLE_FILE_STATUSES} table contains a failure for the HL7 file repeated twice.
     * The {@value #TABLE_HL7_FILES} table contains a row for the HL7 file.
     */
    @Test
    public void testStatusDbHl7EmptyError() {
        final String date = "20190106";
        final List<String> workflows = Collections.singletonList(ingestWorkflow.getIngestWorkflowId());

        final FileStatus logRow = FileStatus.parsedLog();
        runLogStatusTest(
            FileStatusLogQuery.tableQuery(date, workflows),
            Collections.nCopies(SPLIT_AND_UPLOAD_RETRIES, logRow)
                .toArray(new FileStatus[0])
        );

        final FileStatus hl7Error = FileStatus.failedHl7(null, "HL7 message content is empty");
        runHl7FileStatusTest(
            FileStatusHl7Query.tableQuery(date, workflows),
            Collections.nCopies(SPLIT_AND_UPLOAD_RETRIES, hl7Error)
                .toArray(new FileStatus[0])
        );
    }

    /**
     * Tests the state of the ingest database after the test data has been processed by Scout.
     * In particular, for the date 2016-08-29, the log file contains an HL7 message with garbage content.
     * The {@value #TABLE_FILE_STATUSES} table should have one "parsed" row for the log file
     * because it ingests properly to "staging" with no retries.
     * The {@value #TABLE_FILE_STATUSES} table contains a "staged" message for the HL7 file then a
     * single failure.
     * The {@value #TABLE_HL7_FILES} table contains a row for the HL7 file.
     */
    @Test
    public void testStatusDbHl7GarbageError() {
        final String date = "20160829";
        final List<String> parentWorkflowId = Collections.singletonList(ingestWorkflow.getIngestWorkflowId());

        final FileStatus logRow = FileStatus.parsedLog();
        runLogStatusTest(
            FileStatusLogQuery.tableQuery(date, parentWorkflowId),
            logRow
        );

        final List<String> ingestWorkflowIds = Stream.concat(
            parentWorkflowId.stream(),
            ingestWorkflow.getIngestToDeltaLakeWorkflows().stream()
        ).toList();

        final String filePath = "2016/08/29/12/201608291211093942_0.hl7";
        final Hl7FilesRow hl7File = new Hl7FilesRow(filePath, 0, date);
        final FileStatus hl7Staged = FileStatus.staged(filePath);
        final FileStatus hl7Error = FileStatus.failedHl7(filePath, "File is not parsable as HL7");
        runHl7FileStatusTest(
            FileStatusHl7Query.tableQuery(date, ingestWorkflowIds),
            hl7Staged,
            hl7Error
        );
        runHl7FilesTest(
            new Hl7FileTableQuery(date),
            hl7File
        );
        runRecentHl7FilesTest(new RecentHl7FilesViewQuery(date, ingestWorkflowIds),
            new RecentHl7FileRow(hl7File, hl7Error)
        );
    }

    /**
     * Tests the state of the ingest database after the test data has been processed by Scout.
     * This test is essentially the same as {@link #testStatusDbSuccess} but the log file has some
     * extra chatter in the logs that was causing an issue in production, which is checked by this test.
     */
    @Test
    public void testStatusDbTcpChatter() {
        final List<String> parentWorkflowId = Collections.singletonList(ingestWorkflow.getIngestWorkflowId());
        final List<String> ingestWorkflowIds = Stream.concat(
            parentWorkflowId.stream(),
            ingestWorkflow.getIngestToDeltaLakeWorkflows().stream()
        ).toList();
        final String date = "19991130";

        final FileStatus logRow = FileStatus.parsedLog();
        runLogStatusTest(FileStatusLogQuery.tableQuery(date, parentWorkflowId), logRow);

        final Hl7FilesRow h0 = new Hl7FilesRow("1999/11/30/02/199911300242267124_0.hl7", 0, date);
        final Hl7FilesRow h1 = new Hl7FilesRow("1999/11/30/23/199911302311298376_1.hl7", 1, date);
        final FileStatus h0Staged = FileStatus.staged(h0.hl7FilePath);
        final FileStatus h0Success = FileStatus.success(h0.hl7FilePath);
        final FileStatus h1Staged = FileStatus.staged(h1.hl7FilePath);
        final FileStatus h1Success = FileStatus.success(h1.hl7FilePath);

        runHl7FilesTest(
            new Hl7FileTableQuery(date),
            h0,
            h1
        );

        runHl7FileStatusTest(
            FileStatusHl7Query.tableQuery(date, ingestWorkflowIds),
            h0Staged,
            h1Staged,
            h0Success,
            h1Success
        );

        runHl7FilesTest(
            new Hl7FileTableQuery(date),
            h0,
            h1
        );
        runRecentHl7FilesTest(
            new RecentHl7FilesViewQuery(date, ingestWorkflowIds),
            new RecentHl7FileRow(h0, h0Success),
            new RecentHl7FileRow(h1, h1Success)
        );
    }

    /**
     * Tests the state of the ingest database after attempting to ingest a non-existent log file.
     * For the date 2015-01-01, we pass a log path that does not exist.
     * The {@value #TABLE_FILE_STATUSES} table is expected to have multiple "failed" log rows
     * for that day from retries.
     */
    @Test
    public void testIngestImproperLogPath() {
        final String date = "20150101";
        final String improperLog = "/data/postgres/" + date + ".log";
        final String workflowId = temporalClient.launchIngest(
            new IngestJobInput().setLogPaths(improperLog),
            false
        ).getIngestWorkflowId();
        final List<String> workflows = Collections.singletonList(workflowId);

        final FileStatus logRow = FileStatus.failedLog("NoSuchFileException: " + improperLog);

        runLogStatusTest(
            FileStatusLogQuery.tableQuery(date, workflows),
            Collections.nCopies(SPLIT_AND_UPLOAD_RETRIES, logRow)
                .toArray(new FileStatus[0])
        );

    }

    private void runDbTest(SqlQuery query, Consumer<ResultSet> resultValidator) {
        final String sql = query.build();
        try (
            final Connection connection = config.getPostgresConfig().getConnection();
            final ResultSet resultSet = connection.prepareStatement(sql).executeQuery()
        ) {
            logger.info("Issuing query: {}", sql);
            resultValidator.accept(resultSet);
        } catch (SQLException sqlException) {
            throw new RuntimeException(sqlException);
        }
    }

    private void runLogStatusTest(SqlQuery sql, FileStatus... expectedLogs) {
        runDbTest(
            sql,
            (resultSet) -> {
                try {
                    for (FileStatus row : expectedLogs) {
                        assertThat(resultSet.next()).as("condition that there are additional rows in table").isTrue();
                        assertThat(resultSet.getString(STATUS)).isEqualTo(row.status);
                        assertThat(resultSet.getString(TYPE)).isEqualTo(row.type);
                        assertThat(resultSet.getString(ERROR_MESSAGE)).isEqualTo(row.errorMessage);
                    }
                    assertThat(resultSet.next()).as("condition that there are additional rows in table").isFalse();
                } catch (SQLException e) {
                    throw new RuntimeException(e);
                }
            }
        );
    }

    private void runHl7FileStatusTest(SqlQuery sql, FileStatus... expectedHl7Files) {
        runDbTest(
            sql,
            (resultSet) -> {
                try {
                    for (FileStatus fileStatus : expectedHl7Files) {
                        assertThat(resultSet.next()).as("condition that there are additional rows in table").isTrue();

                        final String actualPath = resultSet.getString(FILE_PATH);
                        if (fileStatus.filePath == null) {
                            assertThat(actualPath)
                                .as("hl7 path matches {log_path}_{message_number}")
                                .matches(".*/\\d{8}\\.log_\\d+");
                        } else {
                            assertThat(actualPath).startsWith("s3://");
                            assertThat(actualPath).endsWith(fileStatus.filePath);
                        }
                        assertThat(resultSet.getString(STATUS)).isEqualTo(fileStatus.status);
                        assertThat(resultSet.getString(TYPE)).isEqualTo(fileStatus.type);
                        assertThat(resultSet.getString(ERROR_MESSAGE)).isEqualTo(fileStatus.errorMessage);
                    }
                    assertThat(resultSet.next()).as("condition that there are additional rows in table").isFalse();
                } catch (SQLException e) {
                    throw new RuntimeException(e);
                }
            }
        );
    }

    private void runHl7FilesTest(SqlQuery sql, Hl7FilesRow... expectedHl7Files) {
        runDbTest(
            sql,
            (resultSet) -> {
                try {
                    for (int i = 0; i < expectedHl7Files.length; i++) {
                        assertThat(resultSet.next()).as("condition that there are additional rows in table").isTrue();

                        // Find the expected hl7 file object corresponding to this row by message number
                        Hl7FilesRow hl7FileRow = null;
                        for (Hl7FilesRow row : expectedHl7Files) {
                            if (resultSet.getInt(MESSAGE_NUMBER) == row.messageNumber) {
                                hl7FileRow = row;
                                break;
                            }
                        }
                        assertThat(hl7FileRow).as("we can find a matching hl7 row").isNotNull();

                        assertThat(resultSet.getDate(DATE).toString().replaceAll("-", ""))
                            .isEqualTo(hl7FileRow.date);
                        assertThat(resultSet.getInt(MESSAGE_NUMBER)).isEqualTo(hl7FileRow.messageNumber);

                        final String actualPath = resultSet.getString(HL7_FILE_PATH);
                        if (hl7FileRow.hl7FilePath == null) {
                            assertThat(actualPath)
                                .as("hl7 path matches {log_path}_{message_number}")
                                .matches(".*/\\d{8}\\.log_\\d+");
                        } else {
                            assertThat(actualPath).startsWith("s3://");
                            assertThat(actualPath).endsWith(hl7FileRow.hl7FilePath);
                        }
                    }
                    assertThat(resultSet.next()).as("condition that there are additional rows in table").isFalse();
                } catch (SQLException e) {
                    throw new RuntimeException(e);
                }
            }
        );
    }

    private void runRecentHl7FilesTest(SqlQuery sql, RecentHl7FileRow... expectedHl7Files) {
        runDbTest(
            sql,
            (resultSet) -> {
                try {
                    for (int i = 0; i < expectedHl7Files.length; i++) {
                        assertThat(resultSet.next()).as("condition that there are additional rows in table").isTrue();

                        // Find the expected object corresponding to this row by ...
                        RecentHl7FileRow h = null;
                        for (RecentHl7FileRow hl7FileRow : expectedHl7Files) {
                            if (resultSet.getInt(MESSAGE_NUMBER) == hl7FileRow.hl7FilesRow.messageNumber) {
                                h = hl7FileRow;
                                break;
                            }
                        }
                        assertThat(h).as("we can find a matching row").isNotNull();

                        assertThat(resultSet.getString(STATUS)).isEqualTo(h.fileStatus.status);
                        assertThat(resultSet.getString(TYPE)).isEqualTo(h.fileStatus.type);
                        assertThat(resultSet.getString(ERROR_MESSAGE)).isEqualTo(h.fileStatus.errorMessage);

                        assertThat(resultSet.getInt(MESSAGE_NUMBER)).isEqualTo(h.hl7FilesRow.messageNumber);
                        assertThat(resultSet.getDate(DATE).toString().replaceAll("-", ""))
                            .isEqualTo(h.hl7FilesRow.date);

                        final String actualPath = resultSet.getString(FILE_PATH);
                        if (h.hl7FilesRow.hl7FilePath == null) {
                            assertThat(actualPath)
                                    .as("hl7 path matches {log_path}_{message_number}")
                                    .matches(".*/\\d{8}\\.log_\\d+");
                        } else {
                            assertThat(actualPath).startsWith("s3://");
                            assertThat(actualPath).endsWith(h.hl7FilesRow.hl7FilePath);
                        }
                    }
                    assertThat(resultSet.next()).as("condition that there are additional rows in table").isFalse();
                } catch (SQLException e) {
                    throw new RuntimeException(e);
                }
            }
        );
    }

    record FileStatus(String filePath, String status, String type, String errorMessage) {

        private static FileStatus parsedLog() {
            return new FileStatus(null, PARSED, LOG_TYPE, null);
        }

        private static FileStatus staged(String filePath) {
            return new FileStatus(filePath, STAGED, HL7_TYPE, null);
        }

        private static FileStatus success(String filePath) {
            return new FileStatus(filePath, SUCCESS, HL7_TYPE, null);
        }

        private static FileStatus failedLog(String errorMessage) {
            return failed(null, LOG_TYPE, errorMessage);
        }

        private static FileStatus failedHl7(String filePath, String errorMessage) {
            return failed(filePath, HL7_TYPE, errorMessage);
        }

        private static FileStatus failed(String filePath, String type, String errorMessage) {
            return new FileStatus(filePath, FAILED, type, errorMessage);
        }
    }

    record Hl7FilesRow(
        String hl7FilePath,
        int messageNumber,
        String date
    ) {}

    record RecentHl7FileRow(
        Hl7FilesRow hl7FilesRow,
        FileStatus fileStatus
    ) {}

    private abstract static class SqlQuery {
        protected final String tableOrView;
        protected final String filterColumn;
        protected final String logDate;
        protected List<String> filteredWorkflowIds;

        protected SqlQuery(String tableOrView, String filterColumn, String logDate, List<String> filteredWorkflowIds) {
            this.tableOrView = tableOrView;
            this.filterColumn = filterColumn;
            this.logDate = logDate;
            this.filteredWorkflowIds = filteredWorkflowIds;
        }

        protected String buildWorkflowQueryRestriction() {
            return String.format(
                "workflow_id IN (%s)",
                filteredWorkflowIds
                    .stream()
                    .map(x -> "'" + x + "'")
                    .collect(Collectors.joining(", "))
            );
        }


        abstract String build();
    }

    private static class FileStatusLogQuery extends SqlQuery {
        private FileStatusLogQuery(String tableOrView, String logDate, List<String> filteredWorkflowIds) {
            super(tableOrView, FILE_PATH, logDate, filteredWorkflowIds);
        }

        static FileStatusLogQuery tableQuery(String logDate, List<String> filteredWorkflowIds) {
            return new FileStatusLogQuery(TABLE_FILE_STATUSES, logDate, filteredWorkflowIds);
        }

        @Override
        String build() {
            return String.format(
                "SELECT * FROM %s WHERE %s AND %s LIKE '%%/%s.log' ORDER BY processed_at ASC",
                tableOrView,
                buildWorkflowQueryRestriction(),
                filterColumn,
                logDate
            );
        }
    }

    private static class FileStatusHl7Query extends SqlQuery {
        private FileStatusHl7Query(String tableOrView, String logDate, List<String> filteredWorkflowIds) {
            super(tableOrView, LOG_FILE_PATH, logDate, filteredWorkflowIds);
        }

        static FileStatusHl7Query tableQuery(String logDate, List<String> filteredWorkflowIds) {
            return new FileStatusHl7Query(TABLE_FILE_STATUSES, logDate, filteredWorkflowIds);
        }

        @Override
        String build() {
            return String.format(
                "SELECT tv.* FROM %s tv "
                    + "JOIN hl7_files h on h.hl7_file_path = tv.file_path "
                    + "WHERE tv.%s AND h.%s LIKE '%%/%s.log' "
                    + "ORDER BY tv.processed_at, tv.file_path ASC",
                tableOrView,
                buildWorkflowQueryRestriction(),
                filterColumn,
                logDate
            );
        }
    }

    private static class Hl7FileTableQuery extends SqlQuery {
        private Hl7FileTableQuery(String logDate) {
            super(TABLE_HL7_FILES, LOG_FILE_PATH, logDate, Collections.emptyList());
        }

        @Override
        String build() {
            return String.format(
                "SELECT * FROM %s WHERE %s LIKE '%%/%s.log'",
                tableOrView,
                filterColumn,
                logDate
            );
        }
    }

    private static class RecentHl7FilesViewQuery extends SqlQuery {
        private RecentHl7FilesViewQuery(String logDate, List<String> filteredWorkflowIds) {
            super(VIEW_RECENT_HL7_FILES, LOG_FILE_PATH, logDate, filteredWorkflowIds);
        }

        @Override
        String build() {
            return String.format(
                "SELECT * FROM %s WHERE %s AND %s LIKE '%%/%s.log' ORDER BY processed_at ASC",
                tableOrView,
                buildWorkflowQueryRestriction(),
                filterColumn,
                logDate
            );
        }
    }
}
