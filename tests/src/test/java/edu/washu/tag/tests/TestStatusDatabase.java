package edu.washu.tag.tests;

import static org.assertj.core.api.AssertionsForClassTypes.assertThat;

import edu.washu.tag.BaseTest;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.function.Consumer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.annotations.Test;

public class TestStatusDatabase extends BaseTest {

    private static final Logger logger = LoggerFactory.getLogger(TestStatusDatabase.class);
    private static final String TABLE_LOG_FILES = "log_files";
    private static final String VIEW_RECENT_LOG_FILES = "recent_log_files";
    private static final String TABLE_HL7_FILES = "hl7_files";
    private static final String VIEW_RECENT_HL7_FILES = "recent_hl7_files";

    @Test
    public void testStatusDbSuccess() {
        final LogRow logRow = LogRow.success("1995-04-02", null);

        runLogTest(
            SqlQuery.logTableQuery("19950402"),
            logRow
        );

        runLogTest(
            SqlQuery.logViewQuery("19950402"),
            logRow
        );

        final Hl7FileRow firstHl7Message = Hl7FileRow.success(0, "1995/04/02/07/199504020707509258.hl7");
        final Hl7FileRow secondHl7Message = Hl7FileRow.success(1, "1995/04/02/07/199504020930172230.hl7");

        runHl7FileTest(
            SqlQuery.hl7FileTableQuery("19950402"),
            firstHl7Message,
            secondHl7Message
        );

        runHl7FileTest(
            SqlQuery.hl7FileViewQuery("19950402"),
            firstHl7Message,
            secondHl7Message
        );
    }

    @Test
    public void testStatusDbTotalFailure() {
        final LogRow logRowWithRetries = LogRow.success("2024-01-02", null);

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
            SqlQuery.hl7FileTableQuery("20240102"),
            repeatedFailingHl7Message,
            repeatedFailingHl7Message,
            repeatedFailingHl7Message,
            repeatedFailingHl7Message,
            repeatedFailingHl7Message
        );

        runHl7FileTest(
            SqlQuery.hl7FileViewQuery("20240102"),
            repeatedFailingHl7Message
        );
    }

    private void runDbTest(SqlQuery sql, Consumer<ResultSet> resultValidator) {
        try (
            final Connection connection = config.getPostgresConfig().getConnection();
            final ResultSet resultSet = connection.prepareStatement(sql.build()).executeQuery()
        ) {
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
                        assertThat(resultSet.getString("file_path")).endsWith(hl7FileRow.filePath);
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

        private static LogRow success(String date, String errorMessage) {
            return new LogRow("succeeded", date, errorMessage);
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

        private SqlQuery(String tableOrView, String filterColumn, String logDate) {
            this.tableOrView = tableOrView;
            this.filterColumn = filterColumn;
            this.logDate = logDate;
        }

        private static SqlQuery logQuery(String tableOrView, String logDate) {
            return new SqlQuery(tableOrView, "file_path", logDate);
        }

        private static SqlQuery logTableQuery(String logDate) {
            return logQuery(TABLE_LOG_FILES, logDate);
        }

        private static SqlQuery logViewQuery(String logDate) {
            return logQuery(VIEW_RECENT_LOG_FILES, logDate);
        }

        private static SqlQuery hl7FileQuery(String tableOrView, String logDate) {
            return new SqlQuery(tableOrView, "log_file_path", logDate);
        }

        private static SqlQuery hl7FileTableQuery(String logDate) {
            return hl7FileQuery(TABLE_HL7_FILES, logDate);
        }

        private static SqlQuery hl7FileViewQuery(String logDate) {
            return hl7FileQuery(VIEW_RECENT_HL7_FILES, logDate);
        }

        private String build() {
            return String.format(
                "SELECT * FROM %s WHERE %s LIKE '%%/%s.log' ORDER BY processed_at",
                tableOrView,
                filterColumn,
                logDate
            );
        }
    }

}
