import os

import psycopg
from temporalio import activity


_connection_args = None

FILE_STATUSES_COLS = (
    "file_path",
    "type",
    "status",
    "error_message",
    "workflow_id",
    "activity_id",
)
FILE_STATUSES_COPY_SQL = f"""
COPY file_statuses ({", ".join(FILE_STATUSES_COLS)}) FROM STDIN (FORMAT BINARY)
"""


def get_db_connection_args():
    """Connect to the PostgreSQL database."""
    global _connection_args
    if _connection_args is None:
        # Load database configuration from environment variables
        _connection_args = {
            "host": os.getenv("DB_HOST"),
            "port": os.getenv("DB_PORT", "5432"),
            "dbname": os.getenv("DB_NAME"),
            "user": os.getenv("DB_USER"),
            "password": os.getenv("DB_PASSWORD"),
        }
    return _connection_args


def write_errors(
    hl7_files: list[str], error_message: str, workflow_id: str, activity_id: str
) -> None:
    """Write an error message to the database for a list of HL7 files."""
    write_status_to_db(hl7_files, "failed", error_message, workflow_id, activity_id)


def write_successes(hl7_files: list[str], workflow_id: str, activity_id: str) -> None:
    """Write a success status to the database for a list of HL7 files."""
    write_status_to_db(hl7_files, "success", None, workflow_id, activity_id)


def write_status_to_db(
    hl7_files: list[str],
    status: str,
    error_message: str | None,
    workflow_id: str,
    activity_id: str,
) -> None:
    """Write status to the database for a list of HL7 files (use COPY for improved performance)."""
    activity.logger.info(
        "Writing '%s' status to database for %d HL7 files", status, len(hl7_files)
    )

    with psycopg.connect(**get_db_connection_args()) as conn, conn.cursor() as cursor:
        with cursor.copy(FILE_STATUSES_COPY_SQL) as copy:
            for hl7_file in hl7_files:
                copy.write_row(
                    [
                        hl7_file,
                        "HL7",
                        status,
                        error_message,
                        workflow_id,
                        activity_id,
                    ]
                )
