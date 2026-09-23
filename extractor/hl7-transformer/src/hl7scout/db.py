import functools
import os

import botocore.session
import psycopg
from temporalio import activity


_connection_args = None
_iam_auth = False

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


@functools.cache
def _rds_client():
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    return botocore.session.get_session().create_client("rds", region_name=region)


def get_db_connection_args():
    """Connect to the PostgreSQL database.

    With DB_IAM_AUTH set, the password is a fresh RDS IAM auth token on every call
    (tokens expire after 15 minutes) and TLS is verified against the RDS CA bundle.
    """
    global _connection_args, _iam_auth
    if _connection_args is None:
        # Load database configuration from environment variables
        _iam_auth = os.getenv("DB_IAM_AUTH", "").strip().lower() in ("1", "true", "yes")
        _connection_args = {
            "host": os.getenv("DB_HOST"),
            "port": os.getenv("DB_PORT", "5432"),
            "dbname": os.getenv("DB_NAME"),
            "user": os.getenv("DB_USER"),
        }
        if _iam_auth:
            _connection_args["sslmode"] = os.getenv("DB_SSLMODE", "verify-full")
            _connection_args["sslrootcert"] = os.getenv(
                "DB_SSLROOTCERT", "/etc/rds-ca/ca.pem"
            )
        else:
            _connection_args["password"] = os.getenv("DB_PASSWORD")
    if not _iam_auth:
        return _connection_args
    token = _rds_client().generate_db_auth_token(
        DBHostname=_connection_args["host"],
        Port=int(_connection_args["port"]),
        DBUsername=_connection_args["user"],
    )
    return {**_connection_args, "password": token}


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
