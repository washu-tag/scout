package edu.washu.tag.extractor.hl7log.db;

import java.time.LocalDate;

/**
 * Represents a row in the "hl7_file" table of the ingest database.
 */
public record Hl7File(
    String hl7FilePath,
    String logFilePath,
    int messageNumber,
    LocalDate date
) {
    private static final String UPSERT_ON_CONFLICT_SQL =
         "ON CONFLICT (hl7_file_path) "
            + "DO UPDATE SET log_file_path = EXCLUDED.log_file_path, message_number = EXCLUDED.message_number, date = EXCLUDED.date";

    /**
     * Gets the "ON CONFLICT" statement for upserting a record into the "hl7_file" table.
     */
    public static String getUpsertSql() {
        return UPSERT_ON_CONFLICT_SQL;
    }
}
