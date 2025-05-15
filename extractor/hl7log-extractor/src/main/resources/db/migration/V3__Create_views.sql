-- View for log files
CREATE OR REPLACE VIEW recent_log_file_statuses AS
SELECT DISTINCT ON (file_path) *
FROM file_statuses
WHERE type = 'Log'
ORDER BY file_path, processed_at DESC;

-- View for hl7 files
CREATE OR REPLACE VIEW recent_hl7_file_statuses AS
SELECT DISTINCT ON (file_path) *
FROM file_statuses
WHERE type = 'HL7'
ORDER BY file_path, processed_at DESC;

-- View for hl7 files with file information
CREATE OR REPLACE VIEW recent_hl7_files AS
SELECT f.*, h.log_file_path, h.message_number, h.date
FROM recent_hl7_file_statuses f
JOIN hl7_files h ON f.file_path = h.hl7_file_path;
