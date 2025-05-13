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
FILE_STATUSES_INSERT_SQL = f"""
INSERT INTO file_statuses ({", ".join(FILE_STATUSES_COLS)})
VALUES ({", ".join(["%s"]*len(FILE_STATUSES_COLS))})
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
    activity.logger.info("Writing errors to database for %d HL7 files", len(hl7_files))
    # Prepare the rows to insert
    insert_rows = [
        (
            hl7_file,
            "HL7",
            "failed",
            error_message,
            workflow_id,
            activity_id,
        )
        for hl7_file in hl7_files
    ]
    with psycopg.connect(**get_db_connection_args()) as conn, conn.cursor() as cursor:
        cursor.executemany(FILE_STATUSES_INSERT_SQL, insert_rows)
        conn.commit()
