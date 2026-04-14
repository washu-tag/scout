"""
title: Scout Query Tool
description: Execute SQL queries against Scout Delta Lake. Stores full results as
             downloadable CSV files and returns only a summary to the LLM context,
             keeping large result sets out of the conversation window.
author: Scout Team
version: 0.1.0
"""

import csv
import io
import uuid
from datetime import datetime, timezone
from typing import Callable, Any, Awaitable, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Stub data — replace the rows below with representative sample data.
# This CSV is returned for ALL queries when use_mock_data is True.
# Keep the separate scout_query_stub_data.csv file in sync for readability;
# this embedded copy is what actually runs.
# ---------------------------------------------------------------------------
MOCK_DATA_CSV = """\
accession_number,epic_mrn,modality,service_name,message_dt,patient_age,sex,diagnoses_consolidated,report_section_impression
TODO-FILL-IN-STUB-DATA
"""


class Tools:
    """Query Scout Delta Lake and return results as downloadable files."""

    class Valves(BaseModel):
        trino_host: str = Field(
            default="trino",
            description="Trino hostname (used when mock data is disabled)",
        )
        trino_port: int = Field(
            default=8080,
            description="Trino port",
        )
        trino_user: str = Field(
            default="trino",
            description="Trino user",
        )
        trino_catalog: str = Field(
            default="delta",
            description="Trino catalog",
        )
        trino_schema: str = Field(
            default="default",
            description="Trino schema",
        )
        use_mock_data: bool = Field(
            default=True,
            description="Return canned CSV data instead of querying Trino (for POC/demo)",
        )
        max_context_rows: int = Field(
            default=5,
            description="Maximum sample rows to include in the LLM context summary",
        )

    def __init__(self):
        self.valves = self.Valves()

    # ------------------------------------------------------------------
    # LLM-callable tool method
    # ------------------------------------------------------------------
    async def query(
        self,
        sql: str,
        __user__: dict = {},
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
    ) -> str:
        """
        Execute a SQL query against the Scout Delta Lake reports database and
        return a summary of the results. The full result set is saved as a
        downloadable CSV file attached to this message.

        Use this tool whenever you need to look up radiology report data.
        The query should be valid Trino SQL against the reports table in the
        delta.default schema.

        :param sql: The SQL query to execute.
        :return: A text summary of the query results (row count, columns, sample rows).
        """
        await self._emit_status(__event_emitter__, "Executing query…", done=False)

        try:
            if self.valves.use_mock_data:
                rows, columns = self._load_mock_data()
            else:
                rows, columns = await self._execute_trino_query(sql)

            if not rows:
                await self._emit_status(
                    __event_emitter__, "Query complete — no results", done=True
                )
                return "The query returned no results."

            # Build CSV bytes from the result set
            csv_bytes = self._rows_to_csv_bytes(columns, rows)

            # Store as an Open WebUI file and attach to the message
            file_id = await self._store_and_attach_file(
                csv_bytes=csv_bytes,
                filename=self._generate_filename(),
                user_id=__user__.get("id", ""),
                event_emitter=__event_emitter__,
            )

            await self._emit_status(__event_emitter__, "Query complete", done=True)

            # Return a compact summary for the LLM context
            return self._build_summary(columns, rows, file_id)

        except Exception as exc:
            await self._emit_status(
                __event_emitter__, f"Query failed: {exc}", done=True
            )
            return f"Error executing query: {exc}"

    # ------------------------------------------------------------------
    # Data retrieval
    # ------------------------------------------------------------------
    def _load_mock_data(self) -> tuple[list[dict], list[str]]:
        """Parse the embedded CSV constant into a list of row dicts and column names."""
        reader = csv.DictReader(io.StringIO(MOCK_DATA_CSV))
        columns = reader.fieldnames or []
        rows = list(reader)
        return rows, list(columns)

    async def _execute_trino_query(self, sql: str) -> tuple[list[dict], list[str]]:
        """Execute a real Trino query. Placeholder for future implementation."""
        # TODO: Implement using trino-python-client:
        #   from trino.dbapi import connect
        #   conn = connect(
        #       host=self.valves.trino_host,
        #       port=self.valves.trino_port,
        #       user=self.valves.trino_user,
        #       catalog=self.valves.trino_catalog,
        #       schema=self.valves.trino_schema,
        #   )
        #   cursor = conn.cursor()
        #   cursor.execute(sql)
        #   columns = [desc[0] for desc in cursor.description]
        #   rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        #   return rows, columns
        raise NotImplementedError(
            "Live Trino queries are not yet implemented. "
            "Enable use_mock_data in Valves to use canned data."
        )

    # ------------------------------------------------------------------
    # File storage
    # ------------------------------------------------------------------
    def _rows_to_csv_bytes(self, columns: list[str], rows: list[dict]) -> bytes:
        """Serialize rows to CSV bytes."""
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        return buf.getvalue().encode("utf-8")

    def _generate_filename(self) -> str:
        """Generate a timestamped filename for the results CSV."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"scout_results_{ts}.csv"

    async def _store_and_attach_file(
        self,
        csv_bytes: bytes,
        filename: str,
        user_id: str,
        event_emitter: Optional[Callable] = None,
    ) -> str:
        """
        Store CSV bytes as an Open WebUI file and emit a files event to
        attach it to the current message.

        Returns the file ID.
        """
        from open_webui.models.files import Files, FileForm
        from open_webui.storage.provider import Storage

        file_id = str(uuid.uuid4())

        # Upload to the configured storage backend.
        # NOTE: The Storage.upload_file signature is based on Open WebUI source
        # code and may need adjustment for your version. If the call fails,
        # check the signature in the running container:
        #   kubectl exec -n ollama deploy/open-webui -- \
        #     python -c "from open_webui.storage.provider import Storage; help(Storage.upload_file)"
        _contents, file_path = Storage.upload_file(
            io.BytesIO(csv_bytes),
            filename,
        )

        # Create the database record
        file_meta = {
            "name": filename,
            "content_type": "text/csv",
            "size": len(csv_bytes),
        }
        Files.insert_new_file(
            user_id,
            FileForm(
                id=file_id,
                filename=filename,
                path=file_path,
                meta=file_meta,
            ),
        )

        # Attach the file to the chat message
        if event_emitter:
            await event_emitter(
                {
                    "type": "files",
                    "data": {
                        "files": [
                            {
                                "type": "file",
                                "id": file_id,
                                "name": filename,
                                "content_type": "text/csv",
                                "size": len(csv_bytes),
                            }
                        ]
                    },
                }
            )

        return file_id

    # ------------------------------------------------------------------
    # LLM context summary
    # ------------------------------------------------------------------
    def _build_summary(self, columns: list[str], rows: list[dict], file_id: str) -> str:
        """
        Build a compact text summary of the results for the LLM context.
        Only includes a few sample rows; the full data is in the attached file.
        """
        n_rows = len(rows)
        n_sample = min(self.valves.max_context_rows, n_rows)
        sample = rows[:n_sample]

        # Format sample rows as a simple aligned table
        sample_lines = []
        for row in sample:
            values = [str(row.get(c, "")) for c in columns]
            sample_lines.append(" | ".join(values))

        header = " | ".join(columns)
        separator = "-+-".join("-" * len(c) for c in columns)
        sample_table = "\n".join([header, separator] + sample_lines)

        parts = [
            f"Query returned {n_rows} row{'s' if n_rows != 1 else ''} "
            f"across {len(columns)} columns.",
            f"Columns: {', '.join(columns)}",
            "",
            f"Sample ({n_sample} of {n_rows} rows):",
            sample_table,
        ]

        if n_rows > n_sample:
            parts.append(
                f"\n... and {n_rows - n_sample} more rows in the attached CSV file."
            )

        parts.append(
            "\nFull results are saved as a downloadable CSV file "
            "visible to the user. Reference this summary when discussing "
            "the data; do not attempt to reproduce the full dataset."
        )

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Event helpers
    # ------------------------------------------------------------------
    @staticmethod
    async def _emit_status(
        emitter: Optional[Callable], description: str, done: bool
    ) -> None:
        """Emit a status event if an emitter is available."""
        if emitter:
            await emitter(
                {
                    "type": "status",
                    "data": {"description": description, "done": done},
                }
            )
