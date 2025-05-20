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
