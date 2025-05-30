-- Function to upsert current status
CREATE OR REPLACE FUNCTION update_current_status()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO current_file_statuses (
        file_path, type, status, error_message, workflow_id, activity_id, processed_at, source_record_id
    ) VALUES (
        NEW.file_path, NEW.type, NEW.status, NEW.error_message,
        NEW.workflow_id, NEW.activity_id, NEW.processed_at, NEW.id
    )
    ON CONFLICT (file_path) DO UPDATE SET
        type = NEW.type,
        status = NEW.status,
        error_message = NEW.error_message,
        workflow_id = NEW.workflow_id,
        activity_id = NEW.activity_id,
        processed_at = NEW.processed_at,
        source_record_id = NEW.id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger to automatically update current status
CREATE TRIGGER trigger_update_current_status
    AFTER INSERT ON file_statuses
    FOR EACH ROW
    EXECUTE FUNCTION update_current_status();
