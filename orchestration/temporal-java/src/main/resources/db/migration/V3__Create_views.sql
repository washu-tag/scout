-- View for log_files
CREATE OR REPLACE VIEW recent_log_files AS
SELECT *
FROM log_files l
WHERE processed_at = (
    SELECT MAX(processed_at)
    FROM log_files
    WHERE file_path = l.file_path
);

-- View for hl7_files
CREATE OR REPLACE VIEW recent_hl7_files AS
SELECT *
FROM hl7_files h
WHERE processed_at = (
    SELECT MAX(processed_at)
    FROM hl7_files
    WHERE file_path = h.file_path
);
