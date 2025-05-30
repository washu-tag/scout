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
   hl7_file_path TEXT PRIMARY KEY,
   log_file_path TEXT,
   message_number INT,
   date DATE,
   UNIQUE (log_file_path, message_number)
);

CREATE TABLE current_file_statuses (
   file_path TEXT PRIMARY KEY,
   type TEXT NOT NULL,
   status TEXT NOT NULL,
   error_message TEXT,
   workflow_id TEXT,
   activity_id TEXT,
   processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
   source_record_id INTEGER REFERENCES file_statuses(id)
);
