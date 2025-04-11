package edu.washu.tag.temporal.db;

import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.util.List;
import org.springframework.jdbc.core.BatchPreparedStatementSetter;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

@Service
public class IngestDbServiceImpl implements IngestDbService {
    private final JdbcTemplate jdbcTemplate;

    public IngestDbServiceImpl(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    @Override
    public void insertLogFile(LogFile logFile) {
        String insertSql = SqlStatementCache.getInsertSql(LogFile.class);
        jdbcTemplate.update(insertSql, SqlStatementCache.extractValues(logFile));
    }

    @Override
    public void batchInsertHl7Files(List<Hl7File> hl7Files) {
        if (hl7Files == null || hl7Files.isEmpty()) {
            return;
        }

        String insertSql = SqlStatementCache.getInsertSql(Hl7File.class);
        jdbcTemplate.batchUpdate(
            insertSql,
            new BatchPreparedStatementSetter() {
                @Override
                public void setValues(PreparedStatement ps, int i) throws SQLException {
                    Hl7File object = hl7Files.get(i);
                    Object[] values = SqlStatementCache.extractValues(object);
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