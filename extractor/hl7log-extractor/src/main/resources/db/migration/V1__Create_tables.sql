CREATE TABLE file_statuses (
   id SERIAL PRIMARY KEY,
   file_path TEXT,
   type TEXT,
   status TEXT,
   error_message TEXT,
   workflow_id TEXT,
   activity_id TEXT,
   processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE hl7_files (
   hl7_file_path TEXT,
   log_file_path TEXT,
   message_number INT,
   date DATE,
   UNIQUE (log_file_path, message_number)
);