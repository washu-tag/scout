package edu.washu.tag.extractor.hl7log.db;

import com.zaxxer.hikari.HikariDataSource;
import io.temporal.activity.Activity;
import io.temporal.workflow.Workflow;
import jakarta.annotation.PostConstruct;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.List;
import org.slf4j.Logger;
import org.springframework.jdbc.core.BatchPreparedStatementSetter;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;

@Service
public class IngestDbServiceImpl implements IngestDbService {
    private static final Logger logger = Workflow.getLogger(IngestDbServiceImpl.class);

    private final JdbcTemplate jdbcTemplate;
    private final NamedParameterJdbcTemplate namedParameterJdbcTemplate;

    public IngestDbServiceImpl(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
        this.namedParameterJdbcTemplate = new NamedParameterJdbcTemplate(jdbcTemplate);
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

    @Override
    public List<Hl7FileLogFileSegmentNumber> queryHl7FileLogFileSegmentNumbers(List<String> hl7FilePaths) {
        String querySql = "SELECT DISTINCT file_path, log_file_path, segment_number FROM hl7_files WHERE file_path IN (:hl7FilePaths)";
        MapSqlParameterSource parameters = new MapSqlParameterSource();
        parameters.addValue("hl7FilePaths", hl7FilePaths);

        return namedParameterJdbcTemplate.query(querySql, parameters, (rs, rowNum) -> {
            String hl7FilePath = rs.getString("file_path");
            String logFilePath = rs.getString("log_file_path");
            int segmentNumber = rs.getInt("segment_number");
            return new Hl7FileLogFileSegmentNumber(hl7FilePath, logFilePath, segmentNumber);
        });
    }
}
