-- Indexes for file_statuses
CREATE INDEX idx_file_statuses_file_path ON file_statuses (file_path);
CREATE INDEX idx_file_statuses_file_path_type ON file_statuses (file_path, type);
CREATE INDEX idx_file_statuses_status_type ON file_statuses (status, type);
CREATE INDEX idx_file_statuses_workflow_id ON file_statuses(workflow_id);

-- Indexes for hl7_files
CREATE INDEX idx_hl7_files_log_file_path ON hl7_files (log_file_path);
CREATE INDEX idx_hl7_files_hl7_file_path ON hl7_files (hl7_file_path);
CREATE INDEX idx_hl7_files_date ON hl7_files (date);
