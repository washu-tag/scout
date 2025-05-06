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
    private static final String TABLE_LOG_FILES = "log_files";
    private static final String VIEW_RECENT_LOG_FILES = "recent_log_files";
    private static final String TABLE_HL7_FILES = "hl7_files";
    private static final String VIEW_RECENT_HL7_FILES = "recent_hl7_files";

    /**
     * Tests the state of the ingest database after the test data has been processed by Scout.
     * In particular, for the date 1995-04-02, there should be 2 HL7 messages represented by the log file.
     * The {@value #TABLE_LOG_FILES} table and {@value #VIEW_RECENT_LOG_FILES} view should have a single
     * successful row corresponding to that date, while the {@value #TABLE_HL7_FILES} table and
     * {@value #VIEW_RECENT_HL7_FILES} view should contain 2 rows for that date, one per file.
     */
    @Test
    public void testStatusDbSuccess() {
        final LogRow logRow = LogRow.success("1995-04-02");

        runLogTest(
            SqlQuery.logTableQuery("19950402"),
            logRow
        );

        runLogTest(
            SqlQuery.logViewQuery("19950402"),
            logRow
        );

        final Hl7FileRow firstHl7Message = Hl7FileRow.success(0, "1995/04/02/07/199504020707509258.hl7");
        final Hl7FileRow secondHl7Message = Hl7FileRow.success(1, "1995/04/02/09/199504020930172230.hl7");

        runHl7FileTest(
            SqlQuery.hl7FileTableQuery("19950402", Collections.singletonList(ingestWorkflowId)),
            firstHl7Message,
            secondHl7Message
        );

        runHl7FileTest(
            SqlQuery.hl7FileViewQuery("19950402", Collections.singletonList(ingestWorkflowId)),
            firstHl7Message,
            secondHl7Message
        );
    }

    /**
     * Tests the state of the ingest database after the test data has been processed by Scout.
     * In particular, for the date 2024-01-02, the entire content of the log file is unusable by Scout.
     * The {@value #TABLE_LOG_FILES} table is expected to have five "successful" rows for that day
     * from retries because the error is tracked later on while the {@value #VIEW_RECENT_LOG_FILES} view
     * limits to a single row. The {@value #TABLE_HL7_FILES} table contains the 5 failed rows for that date
     * with the {@value #VIEW_RECENT_HL7_FILES} view restricting to a single row.
     */
    @Test
    public void testStatusDbTotalFailure() {
        final LogRow logRowWithRetries = LogRow.success("2024-01-02");

        runLogTest(
            SqlQuery.logTableQuery("20240102"),
            logRowWithRetries,
            logRowWithRetries,
            logRowWithRetries,
            logRowWithRetries,
            logRowWithRetries
        );

        runLogTest(
            SqlQuery.logViewQuery("20240102"),
            logRowWithRetries
        );

        final Hl7FileRow repeatedFailingHl7Message = Hl7FileRow.failure(0, null, "Split content has fewer than 3 lines");

        runHl7FileTest(
            SqlQuery.hl7FileTableQuery("20240102", Collections.singletonList(ingestWorkflowId)),
            repeatedFailingHl7Message,
            repeatedFailingHl7Message,
            repeatedFailingHl7Message,
            repeatedFailingHl7Message,
            repeatedFailingHl7Message
        );

        runHl7FileTest(
            SqlQuery.hl7FileViewQuery("20240102", Collections.singletonList(ingestWorkflowId)),
            repeatedFailingHl7Message
        );
    }

    /**
     * Tests the state of the ingest database after the test data has been processed by Scout.
     * In particular, for the date 2023-01-13, the log file contains an unusable HL7 message followed by
     * 2 usable ones. The {@value #TABLE_LOG_FILES} table and {@value #VIEW_RECENT_LOG_FILES} view should have
     * a single successful row corresponding to that date. The {@value #TABLE_HL7_FILES} table contains
     * a successful row for the improper message and 2 rows for the valid messages under the overall workflow id,
     * with a failing row for the improper message under one of the delta lake ingest child workflows. The
     * {@value #VIEW_RECENT_HL7_FILES} view contains the same row except for the successful row for the improper
     * HL7 message.
     */
    @Test
    public void testStatusDbHl7MessageParseError() {
        final LogRow logRow = LogRow.success("2023-01-13");

        runLogTest(
            SqlQuery.logTableQuery("20230113"),
            logRow
        );

        runLogTest(
            SqlQuery.logViewQuery("20230113"),
            logRow
        );

        final String failedFilePath = "2023/01/13/12/202301131207178754.hl7";
        final Hl7FileRow firstMessageSuccessful = Hl7FileRow.success(0, failedFilePath);
        final Hl7FileRow secondHl7Message = Hl7FileRow.success(1, "2023/01/13/18/202301131807178754.hl7");
        final Hl7FileRow thirdHl7Message = Hl7FileRow.success(2, "2023/01/13/20/202301132017309482.hl7");
        final Hl7FileRow firstMessageFailed = Hl7FileRow.failure(0, failedFilePath, "HL7 file is empty or unparsable");

        runHl7FileTest(
            SqlQuery.hl7FileTableQuery("20230113", Collections.singletonList(ingestWorkflowId)),
            firstMessageSuccessful,
            secondHl7Message,
            thirdHl7Message
        );

        runHl7FileTest(
            SqlQuery.hl7FileTableQuery("20230113", ingestToDeltaLakeWorkflows),
            firstMessageFailed
        );

        runHl7FileTest(
            SqlQuery.hl7FileViewQuery("20230113", Collections.singletonList(ingestWorkflowId)),
            secondHl7Message,
            thirdHl7Message
        );

        runHl7FileTest(
            SqlQuery.hl7FileViewQuery("20230113", ingestToDeltaLakeWorkflows),
            firstMessageFailed
        );
    }

    /**
     * Tests the state of the ingest database after the test data has been processed by Scout.
     * This test is essentially the same as {@link #testStatusDbSuccess} but the log file has some extra
     * chatter in the logs that was causing an issue in production, which is checked by this test.
     */
    @Test
    public void testStatusDbTcpChatter() {
        final LogRow logRow = LogRow.success("1999-11-30");

        runLogTest(
            SqlQuery.logTableQuery("19991130"),
            logRow
        );

        runLogTest(
            SqlQuery.logViewQuery("19991130"),
            logRow
        );

        final Hl7FileRow firstHl7Message = Hl7FileRow.success(0, "1999/11/30/02/199911300242267124.hl7");
        final Hl7FileRow secondHl7Message = Hl7FileRow.success(1, "1999/11/30/23/199911302311298376.hl7");

        runHl7FileTest(
            SqlQuery.hl7FileTableQuery("19991130", Collections.singletonList(ingestWorkflowId)),
            firstHl7Message,
            secondHl7Message
        );

        runHl7FileTest(
            SqlQuery.hl7FileViewQuery("19991130", Collections.singletonList(ingestWorkflowId)),
            firstHl7Message,
            secondHl7Message
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

    private void runLogTest(SqlQuery sql, LogRow... expectedLogs) {
        runDbTest(
            sql,
            (resultSet) -> {
                try {
                    for (LogRow row : expectedLogs) {
                        resultSet.next();
                        assertThat(resultSet.getString("status")).isEqualTo(row.status);
                        assertThat(resultSet.getString("date")).isEqualTo(row.date);
                        assertThat(resultSet.getString("error_message")).isEqualTo(row.errorMessage);
                    }
                    assertThat(resultSet.next()).as("condition that there are additional rows in table").isFalse();
                } catch (SQLException e) {
                    throw new RuntimeException(e);
                }
            }
        );
    }

    private void runHl7FileTest(SqlQuery sql, Hl7FileRow... expectedHl7Files) {
        runDbTest(
            sql,
            (resultSet) -> {
                try {
                    for (Hl7FileRow hl7FileRow : expectedHl7Files) {
                        resultSet.next();
                        assertThat(resultSet.getString("status")).isEqualTo(hl7FileRow.status);
                        assertThat(resultSet.getInt("segment_number")).isEqualTo(hl7FileRow.segmentNumber);
                        final String actualPath = resultSet.getString("file_path");
                        if (hl7FileRow.filePath == null) {
                            assertThat(actualPath).as("file_path").isNull();
                        } else {
                            assertThat(actualPath).startsWith("s3://");
                            assertThat(actualPath).endsWith(hl7FileRow.filePath);
                        }
                        assertThat(resultSet.getString("error_message")).isEqualTo(hl7FileRow.errorMessage);
                    }
                    assertThat(resultSet.next()).as("condition that there are additional rows in table").isFalse();
                } catch (SQLException e) {
                    throw new RuntimeException(e);
                }
            }
        );
    }

    private static class LogRow {
        private final String status;
        private final String date;
        private final String errorMessage;

        private LogRow(String status, String date, String errorMessage) {
            this.status = status;
            this.date = date;
            this.errorMessage = errorMessage;
        }

        private static LogRow success(String date) {
            return new LogRow("succeeded", date, null);
        }
    }

    private static class Hl7FileRow {
        private final String status;
        private final int segmentNumber;
        private final String filePath;
        private final String errorMessage;

        private Hl7FileRow(String status, int segmentNumber, String filePath, String errorMessage) {
            this.status = status;
            this.segmentNumber = segmentNumber;
            this.filePath = filePath;
            this.errorMessage = errorMessage;
        }

        private static Hl7FileRow success(int segmentNumber, String filePath) {
            return new Hl7FileRow("succeeded", segmentNumber, filePath, null);
        }

        private static Hl7FileRow failure(int segmentNumber, String filePath, String errorMessage) {
            return new Hl7FileRow("failed", segmentNumber, filePath, errorMessage);
        }
    }

    private static class SqlQuery {
        private final String tableOrView;
        private final String filterColumn;
        private final String logDate;
        private final List<String> filteredWorkflowIds;

        private SqlQuery(String tableOrView, String filterColumn, String logDate, List<String> filteredWorkflowIds) {
            this.tableOrView = tableOrView;
            this.filterColumn = filterColumn;
            this.logDate = logDate;
            this.filteredWorkflowIds = filteredWorkflowIds;
        }

        private static SqlQuery logQuery(String tableOrView, String logDate) {
            return new SqlQuery(tableOrView, "file_path", logDate, Collections.singletonList(ingestWorkflowId));
        }

        private static SqlQuery logTableQuery(String logDate) {
            return logQuery(TABLE_LOG_FILES, logDate);
        }

        private static SqlQuery logViewQuery(String logDate) {
            return logQuery(VIEW_RECENT_LOG_FILES, logDate);
        }

        private static SqlQuery hl7FileQuery(String tableOrView, String logDate, List<String> filteredWorkflowIds) {
            return new SqlQuery(tableOrView, "log_file_path", logDate, filteredWorkflowIds);
        }

        private static SqlQuery hl7FileTableQuery(String logDate, List<String> filteredWorkflowIds) {
            return hl7FileQuery(TABLE_HL7_FILES, logDate, filteredWorkflowIds);
        }

        private static SqlQuery hl7FileViewQuery(String logDate, List<String> filteredWorkflowIds) {
            return hl7FileQuery(VIEW_RECENT_HL7_FILES, logDate, filteredWorkflowIds);
        }

        private String build() {
            return String.format(
                "SELECT * FROM %s WHERE %s AND %s LIKE '%%/%s.log' ORDER BY processed_at",
                tableOrView,
                buildWorkflowQueryRestriction(),
                filterColumn,
                logDate
            );
        }

        private String buildWorkflowQueryRestriction() {
            return String.format(
                "workflow_id IN (%s)",
                filteredWorkflowIds
                    .stream()
                    .map(x -> "'" + x + "'")
                    .collect(Collectors.joining(", "))
            );
        }
    }

}
