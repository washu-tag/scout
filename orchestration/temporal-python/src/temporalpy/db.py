import os

import psycopg
from psycopg import sql
from temporalio import activity


_connection_args = None


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
    connection_args = get_db_connection_args()
    with psycopg.connect(**connection_args) as conn, conn.cursor() as cursor:
        # Find existing rows so we can get the log file path and segment number
        # Note: Need to dynamically create the query to have a variable number of placeholders
        cursor.execute(
            sql.SQL(
                """
                SELECT DISTINCT file_path, log_file_path, segment_number
                FROM hl7_files
                WHERE file_path in ({placeholders})
                """
            ).format(
                placeholders=sql.SQL(", ").join([sql.Placeholder()] * len(hl7_files))
            ),
            hl7_files,
        )
        rows = cursor.fetchall()

        # Did we find rows for all the files that were passed in?
        found_hl7_files = {row[0] for row in rows}
        input_hl7_files = set(hl7_files)
        missing_hl7_files = input_hl7_files - found_hl7_files

        # Use the existing log file and segment number for the found HL7 files, placeholders for the missing ones
        insert_rows = [
            (
                log_file_path,
                segment_number,
                hl7_file,
                "failed",
                error_message,
                workflow_id,
                activity_id,
            )
            for hl7_file, log_file_path, segment_number in rows
        ] + [
            (None, -1, hl7_file, "failed", error_message, workflow_id, activity_id)
            for hl7_file in missing_hl7_files
        ]

        # Write new rows
        cursor.executemany(
            """
            INSERT INTO hl7_files (log_file_path, segment_number, file_path, status, error_message, workflow_id, activity_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            insert_rows,
        )

        conn.commit()
