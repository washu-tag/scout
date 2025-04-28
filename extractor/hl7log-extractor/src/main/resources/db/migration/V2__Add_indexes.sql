-- Indexes for log_files
CREATE INDEX idx_log_files_file_path ON log_files (file_path);
CREATE INDEX idx_log_files_processed_at ON log_files (processed_at);
CREATE INDEX idx_log_files_file_path_processed_at ON log_files (file_path, processed_at);

-- Indexes for hl7_files
CREATE INDEX idx_hl7_files_log_file_path ON hl7_files (log_file_path);
CREATE INDEX idx_hl7_files_file_path ON hl7_files (file_path);
CREATE INDEX idx_hl7_files_processed_at ON hl7_files (processed_at);
