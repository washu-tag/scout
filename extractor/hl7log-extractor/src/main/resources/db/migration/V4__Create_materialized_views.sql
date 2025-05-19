-- View for dashboard: hl7 file counts by date and status
CREATE MATERIALIZED VIEW hl7_file_counts AS
SELECT
    date,
    status,
    COUNT(*) as files
FROM recent_hl7_files f
GROUP BY date, status
WITH DATA;

-- Add a unique index to the materialized view (so we can refresh it concurrently)
CREATE UNIQUE INDEX idx_hl7_file_counts_date_status
    ON hl7_file_counts(date, status);

-- View for dashboard: error messages
CREATE MATERIALIZED VIEW error_messages AS
SELECT h.date, fs.*
FROM hl7_files h
JOIN (
    SELECT DISTINCT ON (file_path)
        file_path,
        type,
        error_message,
        status,
        processed_at,
        workflow_id,
        activity_id
    FROM file_statuses
    WHERE status = 'failed'
    ORDER BY file_path, processed_at DESC
) fs ON fs.file_path = h.hl7_file_path
WITH DATA;

-- Add a unique index to the error messages materialized view
CREATE UNIQUE INDEX idx_error_messages_file_path
    ON error_messages(file_path);
