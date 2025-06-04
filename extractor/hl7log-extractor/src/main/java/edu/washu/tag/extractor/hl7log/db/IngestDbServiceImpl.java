package edu.washu.tag.extractor.hl7log.db;

import com.zaxxer.hikari.HikariDataSource;
import edu.washu.tag.extractor.hl7log.db.DbUtils.FileStatusType;
import io.temporal.activity.Activity;
import io.temporal.activity.ActivityInfo;
import io.temporal.workflow.Workflow;
import jakarta.annotation.PostConstruct;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.time.LocalDate;
import java.util.List;
import java.util.stream.IntStream;
import org.slf4j.Logger;
import org.springframework.jdbc.core.BatchPreparedStatementSetter;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

@Service
public class IngestDbServiceImpl implements IngestDbService {
    private static final Logger logger = Workflow.getLogger(IngestDbServiceImpl.class);

    private final JdbcTemplate jdbcTemplate;

    public IngestDbServiceImpl(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    @PostConstruct
    public void postInitLogging() {
        try {
            if (jdbcTemplate.getDataSource() instanceof HikariDataSource hikariDataSource) {
                logger.info("Database URL: {}", hikariDataSource.getJdbcUrl());
                logger.info("Database Username: {}", hikariDataSource.getUsername());
            } else {
                logger.warn("DataSource is not an instance of HikariDataSource. Connection parameters may not be accessible.");
            }
        } catch (Exception e) {
            logger.error("Failed to log database connection parameters", e);
        }
    }

    @Override
    public void insertFileStatus(FileStatus fileStatus) {
        String insertSql = DbUtils.getInsertSql(FileStatus.class);
        jdbcTemplate.update(insertSql, DbUtils.extractValues(fileStatus));
    }

    @Override
    public void batchInsertHl7Files(List<Hl7File> hl7Files) {
        if (hl7Files == null || hl7Files.isEmpty()) {
            return;
        }
        if (logger.isDebugEnabled()) {
            ActivityInfo activityInfo = Activity.getExecutionContext().getInfo();
            logger.debug("WorkflowId {} ActivityId {} - Inserting {} HL7 file records into database",
                activityInfo.getWorkflowId(), activityInfo.getActivityId(), hl7Files.size());
        }


        String insertSql = DbUtils.getInsertSql(Hl7File.class);
        jdbcTemplate.batchUpdate(
            insertSql,
            new BatchPreparedStatementSetter() {
                @Override
                public void setValues(PreparedStatement ps, int i) throws SQLException {
                    Hl7File object = hl7Files.get(i);
                    Object[] values = DbUtils.extractValues(object);
                    for (int j = 0; j < values.length; j++) {
                        ps.setObject(j + 1, values[j]);
                    }
                }

                @Override
                public int getBatchSize() {
                    return hl7Files.size();
                }
            }
        );
    }

    @Override
    public void batchInsertFileStatuses(List<FileStatus> fileStatuses) {
        if (fileStatuses == null || fileStatuses.isEmpty()) {
            return;
        }
        if (logger.isDebugEnabled()) {
            logger.debug("WorkflowId {} ActivityId {} - Inserting {} file status records into database",
                fileStatuses.getFirst().workflowId(), fileStatuses.getFirst().activityId(), fileStatuses.size());
        }

        String insertSql = DbUtils.getInsertSql(FileStatus.class);
        jdbcTemplate.batchUpdate(
            insertSql,
            new BatchPreparedStatementSetter() {
                @Override
                public void setValues(PreparedStatement ps, int i) throws SQLException {
                    FileStatus object = fileStatuses.get(i);
                    Object[] values = DbUtils.extractValues(object);
                    for (int j = 0; j < values.length; j++) {
                        ps.setObject(j + 1, values[j]);
                    }
                }

                @Override
                public int getBatchSize() {
                    return fileStatuses.size();
                }
            }
        );
    }

    /**
     * Inserts new records into both the hl7_files table and the file_statuses table.
     *
     * @param fileStatuses The list of file statuses to insert.
     * @param logPath The log path associated with the file statuses.
     * @param date The date associated with the file statuses.
     */
    @Override
    public void batchInsertNewHl7FileStatuses(List<FileStatus> fileStatuses, String logPath, LocalDate date) {
        if (fileStatuses == null || fileStatuses.isEmpty()) {
            return;
        }
        batchInsertFileStatuses(fileStatuses);

        // Filter out Log file statuses
        List<FileStatus> hl7FileStatuses = fileStatuses.stream()
            .filter(fileStatus -> FileStatusType.HL7.getType().equals(fileStatus.type()))
            .toList();
        // Create Hl7File records from the filtered file statuses, assigning the message number based on the index in the list
        List<Hl7File> hl7Files = IntStream.range(0, hl7FileStatuses.size())
            .mapToObj(i -> new Hl7File(hl7FileStatuses.get(i).filePath(), logPath, i, date))
            .toList();
        batchInsertHl7Files(hl7Files);
    }
}
