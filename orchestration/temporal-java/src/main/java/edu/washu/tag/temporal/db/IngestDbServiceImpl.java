package edu.washu.tag.temporal.db;

import com.zaxxer.hikari.HikariDataSource;
import io.temporal.workflow.Workflow;
import jakarta.annotation.PostConstruct;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.util.List;
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
    public void insertLogFile(LogFile logFile) {
        logger.debug("WorkflowId {} ActivityId {} - Inserting log file {} record into database", logFile.workflowId(), logFile.activityId(), logFile.filePath());
        String insertSql = DbUtils.getInsertSql(LogFile.class);
        jdbcTemplate.update(insertSql, DbUtils.extractValues(logFile));
    }

    @Override
    public void batchInsertHl7Files(List<Hl7File> hl7Files) {
        if (hl7Files == null || hl7Files.isEmpty()) {
            return;
        }

        logger.debug("WorkflowId {} ActivityId {} - Inserting {} HL7 file records into database", hl7Files.getFirst().workflowId(),
            hl7Files.getFirst().activityId(), hl7Files.size());

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
}