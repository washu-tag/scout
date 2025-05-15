-- View for dashboard: hl7 file counts by date and status
CREATE MATERIALIZED VIEW hl7_file_counts AS
SELECT
    date,
    status,
    COUNT(*) as files
FROM recent_hl7_files f
GROUP BY date, status;

-- View for dashboard: error messages
CREATE MATERIALIZED VIEW error_messages AS
SELECT h.date,
       fs.*
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
) AS fs
    ON fs.file_path = h.hl7_file_path;

-- Add a timestamp column to track when the view was last refreshed
CREATE TABLE view_refresh_log (
    view_name TEXT PRIMARY KEY,
    last_refreshed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    refresh_duration_ms INTEGER
);

-- Store procedure to refresh views and log the operation
CREATE OR REPLACE PROCEDURE refresh_materialized_views()
    LANGUAGE plpgsql
AS $$
DECLARE
    start_time TIMESTAMP;
    duration_ms INTEGER;
BEGIN
    -- Refresh the status counts view
    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY hl7_file_counts;
    duration_ms := extract(epoch from (clock_timestamp() - start_time)) * 1000;

    -- Log the refresh
    INSERT INTO view_refresh_log (view_name, last_refreshed, refresh_duration_ms)
    VALUES ('hl7_file_counts', now(), duration_ms)
    ON CONFLICT (view_name) DO UPDATE
        SET last_refreshed = now(), refresh_duration_ms = duration_ms;

    -- Refresh the error messages view
    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY error_messages;
    duration_ms := extract(epoch from (clock_timestamp() - start_time)) * 1000;
    -- Log the refresh
    INSERT INTO view_refresh_log (view_name, last_refreshed, refresh_duration_ms)
    VALUES ('error_messages', now(), duration_ms)
    ON CONFLICT (view_name) DO UPDATE
        SET last_refreshed = now(), refresh_duration_ms = duration_ms;
END;
$$;