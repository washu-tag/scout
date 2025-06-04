package edu.washu.tag.extractor.hl7log.db;

import java.time.LocalDate;

public record Hl7File(
    String hl7FilePath,
    String logFilePath,
    int messageNumber,
    LocalDate date
) {
    private static final String UPSERT_ON_CONFLICT_SQL =
         "ON CONFLICT (hl7_file_path) "
            + "DO UPDATE SET log_file_path = EXCLUDED.log_file_path, message_number = EXCLUDED.message_number, date = EXCLUDED.date";

    public static String getUpsertSql() {
        return UPSERT_ON_CONFLICT_SQL;
    }
}
