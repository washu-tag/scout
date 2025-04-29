CREATE OR REPLACE VIEW recent_hl7_files AS
SELECT *
FROM hl7_files h
WHERE processed_at = (
    SELECT MAX(processed_at)
    FROM hl7_files
    WHERE file_path = h.file_path OR
        (
            file_path IS NULL AND
            h.file_path IS NULL AND
            log_file_path = h.log_file_path AND
            segment_number = h.segment_number
        )
);
