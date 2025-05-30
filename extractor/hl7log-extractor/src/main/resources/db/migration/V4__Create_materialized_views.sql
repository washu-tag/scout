-- View for hl7 files with file information
CREATE MATERIALIZED VIEW recent_hl7_files AS
SELECT f.*, h.log_file_path, h.message_number, h.date
FROM recent_hl7_file_statuses f
JOIN hl7_files h ON f.file_path = h.hl7_file_path
WITH DATA;

-- Add a unique index to the materialized view
CREATE UNIQUE INDEX idx_recent_hl7_files_file_path ON recent_hl7_files(file_path);
CREATE INDEX idx_recent_hl7_files_date ON recent_hl7_files(date);
CREATE INDEX idx_recent_hl7_files_status ON recent_hl7_files(status);
CREATE INDEX idx_recent_hl7_files_workflow_id ON recent_hl7_files(workflow_id);

-- View for dashboard: hl7 file counts by date and status
CREATE MATERIALIZED VIEW hl7_file_counts AS
SELECT
    date,
    status,
    COUNT(*) as files
FROM recent_hl7_files f
GROUP BY date, status
WITH DATA;

-- Add a unique index to the materialized view
CREATE UNIQUE INDEX idx_hl7_file_counts_date_status ON hl7_file_counts(date, status);

-- View for dashboard: error messages
CREATE MATERIALIZED VIEW error_messages AS
SELECT date, file_path, type, error_message, status,
       processed_at, workflow_id, activity_id
FROM recent_hl7_files
WHERE status = 'failed'
WITH DATA;

-- Add a unique index to the error messages materialized view
CREATE UNIQUE INDEX idx_error_messages_file_path ON error_messages(file_path);

-- Create a table to log the refresh status of materialized views
CREATE TABLE view_refresh_log (
    view_name TEXT PRIMARY KEY,
    last_refreshed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    refresh_duration_ms INTEGER
);

-- Helper function to refresh a materialized view and log the operation
CREATE OR REPLACE PROCEDURE refresh_and_log(input_view_name TEXT)
AS $$
DECLARE
    start_time TIMESTAMP;
    duration_ms INTEGER;
BEGIN
    start_time := clock_timestamp();
    EXECUTE format('REFRESH MATERIALIZED VIEW CONCURRENTLY %I', input_view_name);
    duration_ms := extract(epoch FROM (clock_timestamp() - start_time)) * 1000;

    INSERT INTO view_refresh_log (view_name, last_refreshed, refresh_duration_ms)
    VALUES (input_view_name, now(), duration_ms)
    ON CONFLICT (view_name) DO UPDATE
        SET last_refreshed = now(), refresh_duration_ms = duration_ms;
END;
$$ LANGUAGE plpgsql;

-- Store procedure to refresh views and log the operation
CREATE OR REPLACE PROCEDURE refresh_materialized_views()
    LANGUAGE plpgsql
AS $$
BEGIN
    -- Refresh and log the recent HL7 files view
    CALL refresh_and_log('recent_hl7_files');

    -- Refresh and log the status counts view
    CALL refresh_and_log('hl7_file_counts');

    -- Refresh and log the error messages view
    CALL refresh_and_log('error_messages');
END;
$$;
