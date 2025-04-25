CREATE TABLE log_files (
   id SERIAL PRIMARY KEY,
   file_path TEXT,
   date DATE,
   status TEXT,
   error_message TEXT,
   workflow_id TEXT,
   activity_id TEXT,
   processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE hl7_files (
   id SERIAL PRIMARY KEY,
   log_file_path TEXT,
   segment_number INT,
   file_path TEXT,
   status TEXT,
   error_message TEXT,
   workflow_id TEXT,
   activity_id TEXT,
   processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);