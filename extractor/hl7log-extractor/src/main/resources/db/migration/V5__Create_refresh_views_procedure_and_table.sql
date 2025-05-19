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
    -- Refresh and log the status counts view
    CALL refresh_and_log('hl7_file_counts');

    -- Refresh and log the error messages view
    CALL refresh_and_log('error_messages');
END;
$$;
