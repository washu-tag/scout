import psycopg


def write_errors(
    hl7_files: list[str], error_message: str, workflow_id: str, activity_id: str
) -> None:
    """Write an error message to the database for a list of HL7 files."""
    # TODO Get the database connection info from environment variables
    with psycopg.connect() as conn:
        with conn.cursor() as cursor:
            # Find existing rows
            cursor.execute(
                """
                SELECT DISTINCT file_path, log_file_path, segment_number
                FROM hl7_files
                WHERE file_path in %s
                """,
                (hl7_files,),
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
        conn.close()
