package edu.washu.tag.tests;

import static org.assertj.core.api.AssertionsForClassTypes.assertThat;

import edu.washu.tag.BaseTest;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.Collections;
import java.util.List;
import java.util.function.Consumer;
import java.util.stream.Collectors;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.annotations.Test;

public class TestStatusDatabase extends BaseTest {

    private static final Logger logger = LoggerFactory.getLogger(TestStatusDatabase.class);
    private static final String TABLE_FILE_STATUSES = "file_statuses";
    private static final String VIEW_RECENT_LOG_FILE_STATUSES = "recent_log_file_statuses";
    private static final String VIEW_RECENT_HL7_FILE_STATUSES = "recent_hl7_file_statuses";
    private static final String TABLE_HL7_FILES = "hl7_files";
    private static final String VIEW_RECENT_HL7_FILES = "recent_hl7_files";

    private static final String FILE_PATH = "file_path";
    private static final String LOG_FILE_PATH = "log_file_path";
    private static final String HL7_FILE_PATH = "hl7_file_path";
    private static final String MESSAGE_NUMBER = "message_number";
    private static final String DATE = "date";

    private static final String PARSED = "parsed";
    private static final String STAGED = "staged";
    private static final String FAILED = "failed";
    private static final String LOG_TYPE = "Log";
    private static final String HL7_TYPE = "HL7";
    public static final String STATUS = "status";
    public static final String TYPE = "type";
    public static final String ERROR_MESSAGE = "error_message";

    /**
     * Tests the state of the ingest database after the test data has been processed by Scout.
     * In particular, for the date 1995-04-02, there should be 2 HL7 messages represented by the log file.
     * The {@value #TABLE_FILE_STATUSES} table and {@value #VIEW_RECENT_LOG_FILE_STATUSES} view should have
     * a single successful row with a `file_path` corresponding to that date, while the {@value #TABLE_HL7_FILES} table and
     * {@value #VIEW_RECENT_HL7_FILES} view should contain 2 rows for that date, one per file.
     */
    @Test
    public void testStatusDbSuccess() {
        final List<String> workflows = Collections.singletonList(ingestWorkflow.getIngestWorkflowId());
        final String date = "19950402";

        final FileStatus logRow = FileStatus.parsedLog();
        runLogStatusTest(FileStatusLogQuery.tableQuery(date, workflows), logRow);
        runLogStatusTest(FileStatusLogQuery.viewQuery(date, workflows), logRow);

        final RecentHl7FileRow h0 = RecentHl7FileRow.staged("1995/04/02/07/199504020707509258.hl7", 0, date);
        final RecentHl7FileRow h1 = RecentHl7FileRow.staged("1995/04/02/09/199504020930172230.hl7", 1, date);

        runHl7FilesTest(
            new Hl7FileTableQuery(date, workflows),
            h0.hl7FilesRow,
            h1.hl7FilesRow
        );

        runHl7FileStatusTest(
            FileStatusHl7Query.tableQuery(date, workflows),
            h0.fileStatus,
            h1.fileStatus
        );
        runHl7FileStatusTest(
            FileStatusHl7Query.viewQuery(date, workflows),
            h0.fileStatus,
            h1.fileStatus
        );

        runRecentHl7FilesTest(
            new RecentHl7FilesViewQuery(date, workflows),
            h0,
            h1
        );
    }

    /**
     * Tests the state of the ingest database after the test data has been processed by Scout.
     * In particular, for the date 2024-01-02, the entire content of the log file is unusable by Scout.
     * The {@value #TABLE_FILE_STATUSES} table is expected to have four rows for that day—
     * one "{@value #PARSED}" row and one "{@value #FAILED}" row for each of the two retries—while the
     * {@value #VIEW_RECENT_LOG_FILE_STATUSES} view will show only a single "{@value #FAILED}" row.
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
            logRow0,
            logRow1,
            logRow0,
            logRow1
        );
        runLogStatusTest(
            FileStatusLogQuery.viewQuery(date, workflows),
            logRow1
        );

        runHl7FilesTest(new Hl7FileTableQuery(date, workflows));
        runHl7FileStatusTest(FileStatusHl7Query.tableQuery(date, workflows));
        runHl7FileStatusTest(FileStatusHl7Query.viewQuery(date, workflows));

        runRecentHl7FilesTest(new RecentHl7FilesViewQuery(date, workflows));
    }

//    /**
//     * Tests the state of the ingest database after the test data has been processed by Scout.
//     * In particular, for the date 2023-01-13, the log file contains an unusable HL7 message
//     * followed by 2 usable ones.
//     * The {@value #TABLE_LOG_FILES} table and {@value #VIEW_RECENT_LOG_FILES} view should have a single
//     * successful row corresponding to that date. The {@value #TABLE_HL7_FILES} table contains a successful row for the improper message and 2 rows for the
//     * valid messages under the overall workflow id, with a failing row for the improper message under one of the delta lake ingest child workflows. The
//     * {@value #VIEW_RECENT_HL7_FILES} view contains the same row except for the successful row for the improper HL7 message.
//     */
//    @Test
//    public void testStatusDbHl7MessageParseError() {
//        final LogRow logRow = LogRow.success("2023-01-13");
//
//        runLogTest(
//            SqlQuery.logTableQuery("20230113"),
//            logRow
//        );
//
//        runLogTest(
//            SqlQuery.logViewQuery("20230113"),
//            logRow
//        );
//
//        final String failedFilePath = "2023/01/13/12/202301131207178754.hl7";
//        final Hl7FileRow firstMessageSuccessful = Hl7FileRow.success(0, failedFilePath);
//        final Hl7FileRow secondHl7Message = Hl7FileRow.success(1, "2023/01/13/18/202301131807178754.hl7");
//        final Hl7FileRow thirdHl7Message = Hl7FileRow.success(2, "2023/01/13/20/202301132017309482.hl7");
//        final Hl7FileRow firstMessageFailed = Hl7FileRow.failure(0, failedFilePath, "File is not parsable as HL7");
//
//        runHl7FileTest(
//            SqlQuery.hl7FileTableQuery("20230113", Collections.singletonList(ingestWorkflow.getIngestWorkflowId())),
//            firstMessageSuccessful,
//            secondHl7Message,
//            thirdHl7Message
//        );
//
//        runHl7FileTest(
//            SqlQuery.hl7FileTableQuery("20230113", ingestWorkflow.getIngestToDeltaLakeWorkflows()),
//            firstMessageFailed
//        );
//
//        runHl7FileTest(
//            SqlQuery.hl7FileViewQuery("20230113", Collections.singletonList(ingestWorkflow.getIngestWorkflowId())),
//            secondHl7Message,
//            thirdHl7Message
//        );
//
//        runHl7FileTest(
//            SqlQuery.hl7FileViewQuery("20230113", ingestWorkflow.getIngestToDeltaLakeWorkflows()),
//            firstMessageFailed
//        );
//    }

//    /**
//     * Tests the state of the ingest database after the test data has been processed by Scout. In particular, for the date 2007-10-21, the log file contains
//     * an HL7 message with repeated content. The {@value #TABLE_LOG_FILES} table and {@value #VIEW_RECENT_LOG_FILES} view should have a single
//     * successful row corresponding to that date. The {@value #TABLE_HL7_FILES} table contains a successful row for the HL7 and a failure for the repeat
//     * as part of the ingest workflow, and then a successful row for the HL7 as part of the delta lake workflow. The {@value #VIEW_RECENT_HL7_FILES} view
//     * contains one success and one failure.
//     */
//    @Test
//    public void testStatusDbHl7MessageRepeatError() {
//        final LogRow logRow = LogRow.success("2007-10-21");
//        final String date = logRow.date.replaceAll("-", "");
//
//        runLogTest(
//            SqlQuery.logTableQuery(date),
//            logRow
//        );
//
//        runLogTest(
//            SqlQuery.logViewQuery(date),
//            logRow
//        );
//
//        final String filePath = "2007/10/21/15/200710211522316785.hl7";
//        final Hl7FileRow firstMessageSuccessful = Hl7FileRow.success(0, filePath);
//        final Hl7FileRow nextMessageFailed = Hl7FileRow.failure(1, null,
//            "HL7 content did not contain a timestamp header line; this usually means it is a repeat of the previous message's HL7 content");
//
//        runHl7FileTest(
//            SqlQuery.hl7FileTableQuery(date, Collections.singletonList(ingestWorkflow.getIngestWorkflowId())),
//            firstMessageSuccessful,
//            nextMessageFailed
//        );
//
//        runHl7FileTest(
//            SqlQuery.hl7FileViewQuery(date, Collections.singletonList(ingestWorkflow.getIngestWorkflowId())),
//            firstMessageSuccessful,
//            nextMessageFailed
//        );
//    }

//    /**
//     * Tests the state of the ingest database after the test data has been processed by Scout. In particular, for the date 2019-01-06, the log file contains
//     * an HL7 message with no content. The {@value #TABLE_LOG_FILES} table and {@value #VIEW_RECENT_LOG_FILES} view should have a single
//     * successful row corresponding to that date. The {@value #TABLE_HL7_FILES} table contains a failure, as does the {@value #VIEW_RECENT_HL7_FILES} view.
//     */
//    @Test
//    public void testStatusDbHl7EmptyError() {
//        final LogRow logRowWithRetries = LogRow.success("2019-01-06");
//        final String date = logRowWithRetries.date.replaceAll("-", "");
//
//        runLogTest(
//            SqlQuery.logTableQuery(date),
//            logRowWithRetries,
//            logRowWithRetries
//        );
//
//        runLogTest(
//            SqlQuery.logViewQuery(date),
//            logRowWithRetries
//        );
//
//        final Hl7FileRow messageFailed = Hl7FileRow.failure(0, null,
//            "HL7 message content is empty");
//
//        runHl7FileTest(
//            SqlQuery.hl7FileTableQuery(date, Collections.singletonList(ingestWorkflow.getIngestWorkflowId())),
//            messageFailed,
//            messageFailed
//        );
//
//        runHl7FileTest(
//            SqlQuery.hl7FileViewQuery(date, Collections.singletonList(ingestWorkflow.getIngestWorkflowId())),
//            messageFailed
//        );
//    }

//    /**
//     * Tests the state of the ingest database after the test data has been processed by Scout. In particular, for the date 2016-08-29, the log file contains
//     * an HL7 message with garbage content. The {@value #TABLE_LOG_FILES} table and {@value #VIEW_RECENT_LOG_FILES} view should have a single
//     * successful row corresponding to that date. The {@value #TABLE_HL7_FILES} table contains a failure, as does the {@value #VIEW_RECENT_HL7_FILES} view.
//     */
//    @Test
//    public void testStatusDbHl7GarbageError() {
//        final LogRow logRow = LogRow.success("2016-08-29");
//        final String date = logRow.date.replaceAll("-", "");
//
//        runLogTest(
//            SqlQuery.logTableQuery(date),
//            logRow
//        );
//
//        runLogTest(
//            SqlQuery.logViewQuery(date),
//            logRow
//        );
//
//        final String filePath = "2016/08/29/12/201608291211093942.hl7";
//        final Hl7FileRow messageSuccessful = Hl7FileRow.success(0, filePath);
//        final Hl7FileRow messageFailed = Hl7FileRow.failure(0, filePath,
//            "File is not parsable as HL7");
//
//        runHl7FileTest(
//            SqlQuery.hl7FileTableQuery(date, Collections.singletonList(ingestWorkflow.getIngestWorkflowId())),
//            messageSuccessful
//        );
//
//        runHl7FileTest(
//            SqlQuery.hl7FileTableQuery(date, ingestWorkflow.getIngestToDeltaLakeWorkflows()),
//            messageFailed
//        );
//
//        runHl7FileTest(
//            SqlQuery.hl7FileViewQuery(date, List.of(
//                Stream.concat(ingestWorkflow.getIngestToDeltaLakeWorkflows().stream(), Stream.of(ingestWorkflow.getIngestWorkflowId())).toArray(String[]::new)
//            )),
//            messageFailed
//        );
//    }

//    /**
//     * Tests the state of the ingest database after the test data has been processed by Scout. This test is essentially the same as {@link #testStatusDbSuccess}
//     * but the log file has some extra chatter in the logs that was causing an issue in production, which is checked by this test.
//     */
//    @Test
//    public void testStatusDbTcpChatter() {
//        final LogRow logRow = LogRow.success("1999-11-30");
//
//        runLogTest(
//            SqlQuery.logTableQuery("19991130"),
//            logRow
//        );
//
//        runLogTest(
//            SqlQuery.logViewQuery("19991130"),
//            logRow
//        );
//
//        final Hl7FileRow firstHl7Message = Hl7FileRow.success(0, "1999/11/30/02/199911300242267124.hl7");
//        final Hl7FileRow secondHl7Message = Hl7FileRow.success(1, "1999/11/30/23/199911302311298376.hl7");
//
//        runHl7FileTest(
//            SqlQuery.hl7FileTableQuery("19991130", Collections.singletonList(ingestWorkflow.getIngestWorkflowId())),
//            firstHl7Message,
//            secondHl7Message
//        );
//
//        runHl7FileTest(
//            SqlQuery.hl7FileViewQuery("19991130", Collections.singletonList(ingestWorkflow.getIngestWorkflowId())),
//            firstHl7Message,
//            secondHl7Message
//        );
//    }

//    /**
//     * Tests the state of the ingest database after attempting to ingest a non-existent log file.
//     * For the date 2015-01-01, we pass a log path that does not exist. The {@value #TABLE_LOG_FILES} table
//     * is expected to have 2 "failed" rows for that day from retries while the {@value #VIEW_RECENT_LOG_FILES} view
//     * limits to a single row.
//     */
//    @Test
//    public void testIngestImproperLogPath() {
//        final String improperLog = "/data/20150101.log";
//        final String workflowId = temporalClient.launchIngest(
//            new IngestJobInput().setLogPaths(improperLog),
//            false
//        ).getIngestWorkflowId();
//
//        final LogRow logRowWithRetries = LogRow.failed("2015-01-01", "NoSuchFileException: " + improperLog);
//
//        runLogTest(
//            SqlQuery.logTableQuery("20150101").overwriteWorkflowIds(Collections.singletonList(workflowId)),
//            logRowWithRetries,
//            logRowWithRetries
//        );
//
//        runLogTest(
//            SqlQuery.logViewQuery("20150101").overwriteWorkflowIds(Collections.singletonList(workflowId)),
//            logRowWithRetries
//        );
//    }

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
                        resultSet.next();
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
                        resultSet.next();
                        assertThat(resultSet.getString(STATUS)).isEqualTo(fileStatus.status);
                        assertThat(resultSet.getString(TYPE)).isEqualTo(fileStatus.type);
                        assertThat(resultSet.getString(ERROR_MESSAGE)).isEqualTo(fileStatus.errorMessage);

                        final String actualPath = resultSet.getString(FILE_PATH);
                        if (fileStatus.filePath == null) {
                            assertThat(actualPath).as(FILE_PATH).isNull();
                        } else {
                            assertThat(actualPath).startsWith("s3://");
                            assertThat(actualPath).endsWith(fileStatus.filePath);
                        }
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
                    for (Hl7FilesRow hl7FileRow : expectedHl7Files) {
                        resultSet.next();
                        assertThat(resultSet.getInt(MESSAGE_NUMBER)).isEqualTo(hl7FileRow.messageNumber);
                        assertThat(resultSet.getDate(DATE).toString().replaceAll("-", ""))
                            .isEqualTo(hl7FileRow.date);

                        final String actualPath = resultSet.getString(HL7_FILE_PATH);
                        if (hl7FileRow.hl7FilePath == null) {
                            assertThat(actualPath).as(HL7_FILE_PATH).isNull();
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
                    for (RecentHl7FileRow h : expectedHl7Files) {
                        resultSet.next();
                        assertThat(resultSet.getString(STATUS)).isEqualTo(h.fileStatus.status);
                        assertThat(resultSet.getString(TYPE)).isEqualTo(h.fileStatus.type);
                        assertThat(resultSet.getString(ERROR_MESSAGE)).isEqualTo(h.fileStatus.errorMessage);

                        assertThat(resultSet.getInt(MESSAGE_NUMBER)).isEqualTo(h.hl7FilesRow.messageNumber);
                        assertThat(resultSet.getDate(DATE).toString().replaceAll("-", ""))
                            .isEqualTo(h.hl7FilesRow.date);

                        final String actualPath = resultSet.getString(FILE_PATH);
                        if (h.hl7FilesRow.hl7FilePath == null) {
                            assertThat(actualPath).as(FILE_PATH).isNull();
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

        private static FileStatus stagedHl7(String filePath) {
            return new FileStatus(filePath, STAGED, HL7_TYPE, null);
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
    ) {
        static RecentHl7FileRow staged(String filePath, int messageNumber, String date) {
            return new RecentHl7FileRow(
                new Hl7FilesRow(filePath, messageNumber, date),
                FileStatus.stagedHl7(filePath)
            );
        }
    }

    private static abstract class SqlQuery {
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
        static FileStatusLogQuery viewQuery(String logDate, List<String> filteredWorkflowIds) {
            return new FileStatusLogQuery(VIEW_RECENT_LOG_FILE_STATUSES, logDate, filteredWorkflowIds);
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
        static FileStatusHl7Query viewQuery(String logDate, List<String> filteredWorkflowIds) {
            return new FileStatusHl7Query(VIEW_RECENT_HL7_FILE_STATUSES, logDate, filteredWorkflowIds);
        }

        @Override
        String build() {
            return String.format(
                "SELECT tv.* FROM %s tv JOIN hl7_files h on h.hl7_file_path = tv.file_path WHERE tv.%s AND h.%s LIKE '%%/%s.log'",
                tableOrView,
                buildWorkflowQueryRestriction(),
                filterColumn,
                logDate
            );
        }
    }

    private static class Hl7FileTableQuery extends SqlQuery {
        private Hl7FileTableQuery(String logDate, List<String> filteredWorkflowIds) {
            super(TABLE_HL7_FILES, LOG_FILE_PATH, logDate, filteredWorkflowIds);
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
                "SELECT * FROM %s WHERE %s AND %s LIKE '%%/%s.log'",
                tableOrView,
                buildWorkflowQueryRestriction(),
                filterColumn,
                logDate
            );
        }
    }
}
