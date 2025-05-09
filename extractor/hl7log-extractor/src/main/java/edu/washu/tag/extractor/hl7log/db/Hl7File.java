package edu.washu.tag.extractor.hl7log.db;

import java.time.LocalDate;

public record Hl7File(
    String hl7FilePath,
    String logFilePath,
    int messageNumber,
    LocalDate date
) {
    private static final String UPSERT_ON_CONFLICT_SQL =
         "ON CONFLICT (log_file_path, message_number) "
            + "DO UPDATE SET hl7_file_path = EXCLUDED.hl7_file_path, date = EXCLUDED.date";
    public static String getUpsertSql() {
        return UPSERT_ON_CONFLICT_SQL;
    }
}
