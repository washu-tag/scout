"""
title: Scout Query Tool
description: Execute SQL queries against Scout Delta Lake. Stores full results as
             downloadable CSV files and returns only a summary to the LLM context,
             keeping large result sets out of the conversation window.
author: Scout Team
version: 0.1.0
"""

import csv
import html as html_module
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Callable, Any, Awaitable, Optional

from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Stub data — replace the rows below with representative sample data.
# This CSV is returned for ALL queries when use_mock_data is True.
# Keep the separate scout_query_stub_data.csv file in sync for readability;
# this embedded copy is what actually runs.
# ---------------------------------------------------------------------------
MOCK_DATA_CSV = """\
accession_number,epic_mrn,modality,service_name,message_dt,patient_age,sex,diagnoses_consolidated,report_section_impression
ACC-2024-001847,MRN-304821,CT,CT CHEST WITH CONTRAST,2024-03-15 09:22:00,67,M,"J18.9 Pneumonia, unspecified organism","No acute cardiopulmonary process. Stable bilateral pleural effusions. 2mm right upper lobe nodule unchanged from prior."
ACC-2024-002193,MRN-512047,CT,CT HEAD WITHOUT CONTRAST,2024-04-02 14:05:00,45,F,R51.9 Headache unspecified,No acute intracranial abnormality. No evidence of hemorrhage or mass effect.
ACC-2024-003781,MRN-198374,MRI,MRI BRAIN WITH AND WITHOUT CONTRAST,2024-05-18 11:30:00,52,M,"G43.909 Migraine, unspecified","No enhancing lesion. No acute infarct on diffusion-weighted imaging. Mild chronic microvascular ischemic changes."
ACC-2024-004502,MRN-671029,CT,CT ABDOMEN AND PELVIS WITH CONTRAST,2024-06-07 08:45:00,73,F,"K80.20 Calculus of gallbladder without cholecystitis, without obstruction","Cholelithiasis without cholecystitis. No biliary ductal dilation. Incidental 1.5cm left renal cyst."
ACC-2024-005128,MRN-843162,CT,CT CHEST WITH CONTRAST,2024-07-22 16:10:00,58,M,J84.10 Pulmonary fibrosis unspecified,"Interval progression of bilateral lower lobe interstitial fibrosis. No new consolidation. Mediastinal lymphadenopathy stable."
ACC-2024-006294,MRN-425736,MRI,MRI LUMBAR SPINE WITHOUT CONTRAST,2024-08-03 10:15:00,41,F,M54.5 Low back pain,"L4-L5 broad-based disc protrusion with mild bilateral foraminal narrowing. No significant central canal stenosis. Mild facet arthropathy at L5-S1."
ACC-2024-007013,MRN-957201,CT,CT HEAD WITHOUT CONTRAST,2024-08-19 02:33:00,79,M,I63.9 Cerebral infarction unspecified,"Acute right MCA territory infarct. No hemorrhagic transformation. Mild midline shift measuring 3mm."
ACC-2024-008445,MRN-304821,CT,CT CHEST WITHOUT CONTRAST,2024-09-10 13:50:00,67,M,C34.11 Malignant neoplasm of upper lobe right bronchus or lung,"New 1.8cm spiculated right upper lobe mass suspicious for malignancy. Recommend tissue sampling. Bilateral hilar lymphadenopathy."
ACC-2024-009201,MRN-138492,MRI,MRI KNEE LEFT WITHOUT CONTRAST,2024-10-05 09:00:00,34,F,M23.612 Other meniscus derangements lateral meniscus left knee,"Complex tear of the lateral meniscus posterior horn. Moderate joint effusion. ACL intact. Grade II MCL sprain."
ACC-2024-010387,MRN-762810,CT,CT ABDOMEN AND PELVIS WITH CONTRAST,2024-11-14 11:20:00,61,M,"K57.30 Diverticulosis of large intestine without perforation or abscess, without bleeding","Sigmoid diverticulosis without evidence of acute diverticulitis. No free fluid. Hepatic steatosis."
ACC-2024-011052,MRN-519384,CT,CT CHEST WITH CONTRAST,2024-11-28 15:40:00,55,F,I26.99 Other pulmonary embolism without acute cor pulmonale,"Acute pulmonary embolism involving right lower lobe segmental and subsegmental arteries. No right heart strain. Small bilateral pleural effusions."
ACC-2024-012198,MRN-284610,MRI,MRI BRAIN WITH AND WITHOUT CONTRAST,2024-12-09 08:30:00,48,M,D33.0 Benign neoplasm of brain supratentorial,"1.2cm enhancing extra-axial mass along the left convexity consistent with meningioma. No mass effect or edema. Recommend follow-up in 6 months."
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

            # Build the LLM context summary (compact text)
            summary = self._build_summary(columns, rows, file_id)

            # Build the Rich UI HTML table (rendered as iframe for the user)
            rich_ui = self._build_rich_ui(columns, rows, csv_bytes)

            # Returning (HTMLResponse, str) renders the HTML as an iframe
            # for the user while sending only the summary to the LLM context.
            return (
                HTMLResponse(
                    content=rich_ui,
                    headers={"Content-Disposition": "inline"},
                ),
                summary,
            )

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
        # Signature: upload_file(file: BinaryIO, filename: str, tags: Dict[str, str])
        # The tags dict is only used by S3 (when S3_ENABLE_TAGGING is true),
        # but the parameter is required by all storage providers.
        _contents, file_path = Storage.upload_file(
            io.BytesIO(csv_bytes),
            filename,
            {},
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
    # Rich UI
    # ------------------------------------------------------------------
    def _build_rich_ui(
        self, columns: list[str], rows: list[dict], csv_bytes: bytes
    ) -> str:
        """
        Build a self-contained HTML page with an interactive data table and
        a CSV download button. Rendered in a sandboxed iframe by Open WebUI.
        All CSS/JS is inline (no external resources, respects CSP).
        """
        n_rows = len(rows)

        # Build table header
        th_cells = "".join(f"<th>{html_module.escape(c)}</th>" for c in columns)

        # Build table rows
        tr_rows = []
        for row in rows:
            td_cells = "".join(
                f"<td>{html_module.escape(str(row.get(c, '')))}</td>" for c in columns
            )
            tr_rows.append(f"<tr>{td_cells}</tr>")
        tbody = "\n".join(tr_rows)

        # Encode CSV as base64 for the download button
        import base64

        csv_b64 = base64.b64encode(csv_bytes).decode("ascii")

        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 13px;
        color: #e0e0e0;
        background: #1e1e1e;
    }}
    .toolbar {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 8px 12px;
        background: #2a2a2a;
        border-bottom: 1px solid #3a3a3a;
        position: sticky;
        top: 0;
        z-index: 10;
    }}
    .toolbar .info {{
        color: #aaa;
    }}
    .toolbar button {{
        background: #3a3a3a;
        color: #e0e0e0;
        border: 1px solid #555;
        padding: 4px 12px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 12px;
    }}
    .toolbar button:hover {{
        background: #4a4a4a;
    }}
    .table-wrap {{
        overflow: auto;
        max-height: 400px;
    }}
    table {{
        border-collapse: collapse;
        width: 100%;
        white-space: nowrap;
    }}
    th {{
        background: #2a2a2a;
        color: #ccc;
        font-weight: 600;
        position: sticky;
        top: 0;
        z-index: 5;
    }}
    th, td {{
        border: 1px solid #3a3a3a;
        padding: 5px 10px;
        text-align: left;
    }}
    tr:hover {{
        background: #2a2a2a;
    }}
    td {{
        max-width: 300px;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
</style>
</head>
<body>
    <div class="toolbar">
        <span class="info">{n_rows} row{"s" if n_rows != 1 else ""}, {len(columns)} columns</span>
        <button onclick="downloadCsv()">Download CSV</button>
    </div>
    <div class="table-wrap">
        <table>
            <thead><tr>{th_cells}</tr></thead>
            <tbody>{tbody}</tbody>
        </table>
    </div>
<script>
    function downloadCsv() {{
        const b64 = "{csv_b64}";
        const bytes = atob(b64);
        const arr = new Uint8Array(bytes.length);
        for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
        const blob = new Blob([arr], {{ type: "text/csv" }});
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "scout_results.csv";
        a.click();
        URL.revokeObjectURL(url);
    }}
    // Report height to parent for auto-sizing
    function reportHeight() {{
        const h = document.documentElement.scrollHeight;
        parent.postMessage({{ type: "iframe:height", height: Math.min(h, 460) }}, "*");
    }}
    reportHeight();
    new MutationObserver(reportHeight).observe(document.body, {{ childList: true, subtree: true }});
</script>
</body>
</html>"""

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
