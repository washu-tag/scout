# Ingest Status DB
The Ingest Status DB is a database that stores the status of the HL7 report ingestion process. It contains information about
the status of each report, including whether it has been successfully ingested, failed, or is in progress. This information
is used to monitor the progress of the ingestion process and to troubleshoot any issues that may arise.
 
The Ingest Status DB is populated by the {ref}`Extractor service <extractor_ref>` as it processes HL7 files. The process
begins with log files produced by an HL7 listener application from which HL7 files are extracted and staged.
The HL7 files are then ingested into the Delta Lake. The Ingest Status DB tracks the status of each file at each step of this process.

## Database Tables
The Ingest Status DB contains the following tables:
| Table Name | Description |
|------------|-------------|
| `file_statuses` | Contains records of the status of each file as it is processed. This table is append-only, meaning we will write multiple rows for a single file as its status is updated. File records in this table can be for log files and HL7 files. |
| `hl7_files` | Contains a correspondence between an HL7 file and its source log file. |

## file_statuses Table
The `file_statuses` table contains the following columns:
| Column Name | Data Type | Description |
|-------------|-----------|-------------|
| `id` | integer | The unique identifier for the file status record. |
| `file_path` | string | The path to the file. HL7 file paths will be S3 URIs, log file paths will be local paths as seen from within the container. |
| `type` | string | The type of file. Possible values are: `HL7` and `Log`. |
| `status` | string | The status of the file. Possible values are: `parsed`, for log files, `staged` and `ingested` for HL7 files, and `failed` for either. |
| `error_message` | string | The error message associated with the file status, if any. |
| `workflow_id` | string | The ID of the workflow that processed the file. |
| `activity_id` | string | The ID of the activity that processed the file. |
| `processed_at` | timestamp | The timestamp when the file status record was created. |

## hl7_files Table
The `hl7_files` table contains the following columns:
| Column Name | Data Type | Description |
|-------------|-----------|-------------|
| `hl7_file_path` | string | The path to the HL7 file. |
| `log_file_path` | string | The path to the log file. |
| `message_number` | integer | The message within the log file from which the HL7 file was extracted. |
| `date` | date | The date of the log file. |

## Database Views
The Ingest Status DB also contains the following views:
| View Name | Description |
|-----------|-------------|
| `recent_log_file_statuses` | A view that shows the most recent entry in the `file_statuses` table for each log file. |
| `recent_hl7_file_statuses` | A view that shows the most recent entry in the `file_statuses` table for each HL7 file. This view is useful for monitoring the progress of the ingestion process. |
| `recent_hl7_files` | A view that shows the most recent status of each HL7 file along with the columns from the `hl7_files` table. This view is also useful for monitoring progress but is more expensive to create as it requires a large join. |
