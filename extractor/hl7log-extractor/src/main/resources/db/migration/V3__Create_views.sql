-- View for log files
CREATE OR REPLACE VIEW recent_log_file_statuses AS
SELECT *
FROM file_statuses f
WHERE f.type = 'Log' AND processed_at = (
    SELECT MAX(processed_at)
    FROM file_statuses
    WHERE file_path = f.file_path
);

-- View for hl7 files
CREATE OR REPLACE VIEW recent_hl7_file_statuses AS
SELECT *
FROM file_statuses f
WHERE f.type = 'HL7' AND processed_at = (
    SELECT MAX(processed_at)
    FROM file_statuses
    WHERE file_path = f.file_path
);

-- View for hl7 files with file information
CREATE OR REPLACE VIEW recent_hl7_files AS
SELECT f.*, h.log_file_path, h.message_number, h.date
FROM recent_hl7_file_statuses f
JOIN hl7_files h ON f.file_path = h.hl7_file_path;
