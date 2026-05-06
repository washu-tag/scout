"""
title: Scout ID List Tool
description: load_id_list — turn a researcher's CSV/TSV/XLSX upload of patient
             or accession identifiers into a saved Scout cohort that
             search_reports can constrain against via cohort_id=<coh_xxx>.
             Validates IDs against Scout (reports_latest), surfaces unmatched
             entries as a triage list per the Stanford STARR pattern, and
             writes the same recipe + identity protocol used by search_reports.
author: Scout Team
version: 0.2.0
"""

from __future__ import annotations

import asyncio
import base64
import csv
import html as html_module
import inspect
import io
import json
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


# ============================================================================
# Artifacts — inlined from scout_artifacts.py. Same protocol as
# scout_query_tool.py; keep the SCOUT_RESULT_META_KEY in sync.
# ============================================================================

SCOUT_RESULT_META_KEY = "scout_result"


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _write_id_list_artifact(
    recipe: dict, rows: list[dict], unmatched: list, user_id: str, chat_id: str
) -> str:
    if not user_id:
        log.warning("scout_id_list: no user_id; skipping write")
        return ""
    from open_webui.models.files import Files, FileForm
    from open_webui.storage.provider import Storage

    # cohort_id format: `coh_<6 url-safe chars>`. Self-identifying prefix
    # disambiguates from accession_number / message_control_id / MRN in the
    # LLM's context. ~57B combinations — collision-free at our scale.
    file_id = "coh_" + secrets.token_urlsafe(5)[:6]
    filename = f"scout_result_{file_id}.json"
    payload = {
        "file_id": file_id,
        "chat_id": chat_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "recipe": recipe,
        "rows": rows,
        "unmatched": unmatched,
    }
    body = json.dumps(payload, default=str).encode("utf-8")
    _contents, file_path = await _maybe_await(
        Storage.upload_file(io.BytesIO(body), filename, {})
    )
    await _maybe_await(
        Files.insert_new_file(
            user_id,
            FileForm(
                id=file_id,
                filename=filename,
                path=file_path,
                meta={
                    "name": filename,
                    "content_type": "application/json",
                    "size": len(body),
                    "chat_id": chat_id,
                    SCOUT_RESULT_META_KEY: True,
                },
            ),
        )
    )
    return file_id


# ============================================================================
# ID-column / ID-type detection
# ============================================================================

# Column-name patterns to auto-detect the identifier column. Order matters:
# the first pattern with a hit wins. Lower-case match.
ID_COLUMN_PATTERNS = [
    ("epic_mrn", re.compile(r"\b(epic[_\s-]?mrn|epicmrn)\b", re.I)),
    ("epic_mrn", re.compile(r"\bmrn\b", re.I)),
    ("accession_number", re.compile(r"\baccession(?:[_\s-]?number)?\b", re.I)),
    ("message_control_id", re.compile(r"\bmessage[_\s-]?control[_\s-]?id\b", re.I)),
    ("mpi", re.compile(r"\bmpi\b", re.I)),
]

# Allowed id_type parameter values.
VALID_ID_TYPES = {"epic_mrn", "mpi", "accession_number", "message_control_id", "auto"}


def _detect_id_column(headers: list[str], explicit: Optional[str]) -> tuple[str, str]:
    """Return (column_name, id_type). Raises ValueError if nothing matches."""
    if explicit:
        for h in headers:
            if h.strip().lower() == explicit.strip().lower():
                # Caller passes the column name; type still needs guessing
                # below unless they passed an id_type too.
                return h, _guess_type_from_name(h)
        raise ValueError(
            f"id_column={explicit!r} not present in upload headers: {headers!r}"
        )
    for col_type, pattern in ID_COLUMN_PATTERNS:
        for h in headers:
            if pattern.search(h):
                return h, col_type
    raise ValueError(
        f"Could not auto-detect an ID column from headers {headers!r}. "
        f"Supported patterns: epic_mrn / mrn / accession_number / message_control_id / mpi. "
        f"Pass id_column='your_header_name' to override."
    )


def _guess_type_from_name(name: str) -> str:
    lower = name.strip().lower()
    for col_type, pattern in ID_COLUMN_PATTERNS:
        if pattern.search(lower):
            return col_type
    return "epic_mrn"


def _resolve_id_type(detected: str, user_value: str) -> str:
    """Reconcile the auto-detected type with what the user passed."""
    user_value = (user_value or "auto").strip().lower()
    if user_value not in VALID_ID_TYPES:
        raise ValueError(
            f"id_type={user_value!r} must be one of {sorted(VALID_ID_TYPES)}"
        )
    if user_value == "auto":
        return detected
    return user_value


# ============================================================================
# Upload parsing
# ============================================================================


async def _parse_uploaded_files(files: list[dict]) -> tuple[str, list[str], list[dict]]:
    """Return (source_filename, headers, rows) from the first parseable upload.

    OWUI 14.x `__files__` injection only populates `file.data.content` for
    *text-extracted* uploads (RAG-style). For raw CSV/TSV/XLSX attachments,
    `file.data` carries only a `status` field and the actual bytes have to be
    fetched via the Files / Storage API. Try the inline content first, fall
    back to fetching by path."""
    if not files:
        raise ValueError(
            "No file attached. Upload a CSV/TSV/XLSX of identifiers and try again."
        )

    last_err: Optional[str] = None

    for entry in files:
        f = entry.get("file") or entry
        meta = f.get("meta") or entry.get("meta") or {}
        name = (
            meta.get("name")
            or entry.get("name")
            or f.get("filename")
            or entry.get("filename")
            or ""
        )

        # Path 1: in-memory content (RAG / text-extracted uploads).
        data = (f.get("data") or {}).get("content") or entry.get("content")
        if data:
            try:
                return _parse_text_table(name, data)
            except Exception as exc:
                log.warning("inline content parse failed for %s: %s", name, exc)
                last_err = str(exc)

        # Path 2: fetch via OWUI Files / Storage. Works for raw uploads.
        file_id = f.get("id") or entry.get("id") or entry.get("file_id")
        if file_id:
            try:
                content = await _fetch_file_content_by_id(file_id)
                if content:
                    return _parse_text_table(name or f"file_{file_id}", content)
            except Exception as exc:
                log.warning("storage fetch parse failed for %s: %s", file_id, exc)
                last_err = str(exc)

    raise ValueError(
        "Could not parse any of the uploaded files as CSV/TSV. "
        f"{('Last error: ' + last_err) if last_err else ''} "
        "If your upload is XLSX, save it as CSV/TSV first."
    )


async def _fetch_file_content_by_id(file_id: str) -> str:
    """Read a file's text content via OWUI's Files + Storage helpers. Async-aware
    on both sides — OWUI 14.x has a mix of sync and async signatures."""
    from open_webui.models.files import Files
    from open_webui.storage.provider import Storage

    record = await _maybe_await(Files.get_file_by_id(file_id))
    if record is None:
        raise FileNotFoundError(f"File {file_id} not found")
    path = getattr(record, "path", None)
    if not path:
        raise ValueError(f"File {file_id} has no path")

    contents = await _maybe_await(Storage.get_file(path))
    # `get_file` may return a local path string, a file-like, or bytes.
    if isinstance(contents, str):
        with open(contents, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    if hasattr(contents, "read"):
        contents = contents.read()
    if isinstance(contents, bytes):
        return contents.decode("utf-8", errors="replace")
    return str(contents or "")


def _parse_text_table(name: str, content: str) -> tuple[str, list[str], list[dict]]:
    """Sniff CSV vs TSV by header line, then parse via csv.DictReader."""
    lines = content.splitlines()
    if not lines:
        raise ValueError(f"{name!r} appears empty")
    # Sniff: tabs in header → TSV, commas → CSV. Prefer the one with more.
    header = lines[0]
    delim = "\t" if header.count("\t") > header.count(",") else ","
    reader = csv.DictReader(io.StringIO(content), delimiter=delim)
    headers = [h.strip() for h in (reader.fieldnames or [])]
    rows = [dict(r) for r in reader]
    return name, headers, rows


# ============================================================================
# Tools
# ============================================================================


class Tools:
    """Turn an upload into a saved Scout cohort artifact."""

    class Valves(BaseModel):
        trino_host: str = Field(default="trino", description="Trino hostname")
        trino_port: int = Field(default=8080, description="Trino port")
        trino_user: str = Field(default="trino", description="Trino user")
        trino_catalog: str = Field(default="delta", description="Trino catalog")
        trino_schema: str = Field(default="default", description="Trino schema")
        max_unmatched_to_show: int = Field(
            default=20,
            description="Cap on unmatched IDs surfaced in the LLM-bound summary",
        )
        validation_table: str = Field(
            default="delta.default.reports_latest",
            description="Trino table used to validate uploaded IDs against Scout",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def load_id_list(
        self,
        id_column: Optional[str] = None,
        id_type: str = "auto",
        resolve_to_current: bool = True,
        __files__: Optional[list[dict]] = None,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __chat_id__: str = "",
    ) -> Any:
        """
        Parse an attached CSV/TSV/XLSX of patient or accession identifiers,
        validate the IDs against Scout, and save a cohort that
        `search_reports(cohort_id=...)` can constrain queries against.

        Call this tool exactly once per upload session — the returned
        `cohort_id` (`coh_<6 chars>`) is reusable across many subsequent
        `search_reports` calls (e.g. "show me MR PELVIS for this list",
        then "show me CT CHEST for the same list" reuses the same cohort_id
        without re-uploading). Do not call this tool to re-validate a list
        that was already loaded earlier in the chat; cite the prior
        cohort_id instead.

        :param id_column: Optional explicit header name in the upload (case
            insensitive). When omitted, the tool auto-detects from common
            header patterns (mrn, epic_mrn, accession_number,
            message_control_id, mpi).
        :param id_type: Optional override for the identifier type. One of:
            epic_mrn | mpi | accession_number | message_control_id | auto.
            Default "auto".
        :param resolve_to_current: For patient lists, remap stale MPIs to the
            current Epic MRN via the resolved-MRN view (when it lands on the
            sibling branch). No-op today; the parameter is wired so future
            researchers don't need to re-learn the workflow when the view
            ships. Surfaces a one-line notice if the upload looks like stale
            MPIs.
        :return: Confirmation summary including matched/unmatched counts and
            a `cohort_id` you can pass to `search_reports(cohort_id=...)`.
            Always include `{{cohort}}` in your downstream SQL when chaining.
        """
        if __user__ is None:
            __user__ = {}
        if __files__ is None:
            __files__ = []

        await self._emit_status(__event_emitter__, "Parsing upload…", done=False)

        try:
            source_file, headers, rows = await _parse_uploaded_files(__files__)
        except ValueError as exc:
            await self._emit_status(__event_emitter__, str(exc), done=True)
            return f"Error: {exc}"

        try:
            detected_col, detected_type = _detect_id_column(headers, id_column)
            resolved_type = _resolve_id_type(detected_type, id_type)
        except ValueError as exc:
            await self._emit_status(__event_emitter__, str(exc), done=True)
            return f"Error: {exc}"

        raw_values = [str((r.get(detected_col) or "")).strip() for r in rows]
        values = [v for v in raw_values if v]
        unique_values = list(dict.fromkeys(values))  # preserve order, dedupe

        if not unique_values:
            await self._emit_status(
                __event_emitter__, "No non-empty IDs found in upload", done=True
            )
            return f"Error: column {detected_col!r} contained no non-empty values."

        await self._emit_status(
            __event_emitter__,
            f"Validating {len(unique_values)} IDs against Scout…",
            done=False,
        )

        try:
            matched = await self._validate_against_scout(unique_values, resolved_type)
        except Exception as exc:
            log.exception("Trino validation failed")
            await self._emit_status(
                __event_emitter__, f"Validation failed: {exc}", done=True
            )
            return f"Error validating IDs: {exc}"

        matched_set = set(matched)
        unmatched = [v for v in unique_values if v not in matched_set]
        artifact_rows = [{resolved_type: v} for v in unique_values if v in matched_set]

        # Persist saved cohort (chat-scoped) so search_reports / read_reports
        # / Send-to-XNAT can find it.
        saved_cohort_id = ""
        user_id = __user__.get("id", "") if isinstance(__user__, dict) else ""
        if user_id and __chat_id__:
            try:
                recipe = {
                    "source_file": source_file,
                    "id_column": detected_col,
                    "id_type": resolved_type,
                    "resolve_to_current": resolve_to_current,
                }
                saved_cohort_id = await _write_id_list_artifact(
                    recipe=recipe,
                    rows=artifact_rows,
                    unmatched=unmatched,
                    user_id=user_id,
                    chat_id=__chat_id__,
                )
            except Exception:
                log.exception("Failed to persist saved cohort")

        # Resolve-to-current note (stale-MPI detection is a future-branch hook;
        # for now, just emit a one-line marker if the user opted in but the
        # view isn't available).
        resolve_note = ""
        if resolve_to_current and resolved_type in ("mpi", "epic_mrn"):
            resolve_note = (
                "Note: resolve_to_current is currently a no-op — the resolved-MRN "
                "view hasn't shipped yet. Once it does, stale MPIs will remap "
                "automatically."
            )

        await self._emit_status(__event_emitter__, "Upload processed", done=True)

        summary = self._build_summary(
            source_file=source_file,
            id_column=detected_col,
            id_type=resolved_type,
            total=len(unique_values),
            matched=len(matched_set),
            unmatched=unmatched,
            saved_cohort_id=saved_cohort_id,
            resolve_note=resolve_note,
        )
        rich_ui = self._build_ui(
            source_file=source_file,
            id_column=detected_col,
            id_type=resolved_type,
            total=len(unique_values),
            matched=len(matched_set),
            unmatched=unmatched,
            saved_cohort_id=saved_cohort_id,
        )
        return (
            HTMLResponse(content=rich_ui, headers={"Content-Disposition": "inline"}),
            summary,
        )

    # ------------------------------------------------------------------
    # Trino: validate uploaded IDs against the configured table
    # ------------------------------------------------------------------
    async def _validate_against_scout(
        self, values: list[str], id_type: str
    ) -> list[str]:
        if id_type == "mpi":
            # MPI isn't a column in reports_latest yet; without the resolved view,
            # there's nothing to validate against. Return all as matched and let
            # the future view do the work.
            return list(values)
        # Defensive: only allow known column names through to SQL.
        col = {
            "epic_mrn": "epic_mrn",
            "accession_number": "accession_number",
            "message_control_id": "message_control_id",
        }.get(id_type)
        if not col:
            raise ValueError(f"Unsupported id_type for validation: {id_type!r}")

        quoted = ", ".join(f"'{v.replace(chr(39), chr(39) + chr(39))}'" for v in values)
        if len(values) > 5000:
            rows_clause = ", ".join(
                f"('{v.replace(chr(39), chr(39) + chr(39))}')" for v in values
            )
            in_clause = f"{col} IN (SELECT v FROM (VALUES {rows_clause}) AS t(v))"
        else:
            in_clause = f"{col} IN ({quoted})"

        sql = (
            f"SELECT DISTINCT CAST({col} AS varchar) AS v "
            f"FROM {self.valves.validation_table} WHERE {in_clause}"
        )
        rows, _ = await self._execute_trino_query(sql)
        return [str(r.get("v", "")) for r in rows if r.get("v") is not None]

    async def _execute_trino_query(self, sql: str) -> tuple[list[dict], list[str]]:
        import httpx

        base = f"http://{self.valves.trino_host}:{self.valves.trino_port}"
        headers = {
            "X-Trino-User": self.valves.trino_user,
            "X-Trino-Catalog": self.valves.trino_catalog,
            "X-Trino-Schema": self.valves.trino_schema,
            "Content-Type": "text/plain",
        }
        columns: list[str] = []
        rows: list[dict] = []
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            resp = await client.post(
                f"{base}/v1/statement", content=sql, headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
            while True:
                if not columns and "columns" in data:
                    columns = [c["name"] for c in data["columns"]]
                chunk = data.get("data") or []
                for raw_row in chunk:
                    rows.append({columns[i]: raw_row[i] for i in range(len(columns))})
                state = (data.get("stats") or {}).get("state")
                error = data.get("error")
                if error:
                    raise RuntimeError(
                        f"{error.get('message', 'Trino error')} (state={state})"
                    )
                next_uri = data.get("nextUri")
                if not next_uri:
                    break
                resp = await client.get(next_uri, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        return rows, columns

    # ------------------------------------------------------------------
    # Summaries / render
    # ------------------------------------------------------------------
    def _build_summary(
        self,
        source_file: str,
        id_column: str,
        id_type: str,
        total: int,
        matched: int,
        unmatched: list,
        saved_cohort_id: str,
        resolve_note: str,
    ) -> str:
        unmatched_count = len(unmatched)
        head_unmatched = unmatched[: self.valves.max_unmatched_to_show]
        parts: list[str] = []

        # Lead with the cohort_id when saved — most useful for chaining.
        if saved_cohort_id:
            parts.append(f"✓ Saved cohort: cohort_id={saved_cohort_id}")
            parts.append(
                f"  ({matched} {id_type} IDs matched, {unmatched_count} unmatched)"
            )
            parts.append("")
        parts.append(
            f"Loaded {total} unique {id_type} ID{'s' if total != 1 else ''} "
            f"from {source_file!r} (column {id_column!r})."
        )
        parts.append(f"Matched against Scout: {matched}. Unmatched: {unmatched_count}.")
        if saved_cohort_id:
            parts.append("")
            parts.append(
                f"To use this cohort in a search, call "
                f'search_reports(cohort_id={saved_cohort_id!r}, sql="...") '
                f"and include the literal `{{{{cohort}}}}` token where the "
                f"WHERE clause should constrain to this list "
                f"(substitution column will be `{id_type}`)."
            )
        else:
            parts.append(
                "No cohort saved (no chat context available). The upload "
                "won't be chainable in subsequent calls."
            )
        if unmatched_count:
            parts.append("")
            parts.append(
                f"Unmatched IDs (first {len(head_unmatched)} of {unmatched_count}):"
            )
            parts.extend(f"  - {v}" for v in head_unmatched)
            if unmatched_count > len(head_unmatched):
                parts.append(f"  ... and {unmatched_count - len(head_unmatched)} more.")
        if resolve_note:
            parts.append("")
            parts.append(resolve_note)
        return "\n".join(parts)

    def _build_ui(
        self,
        source_file: str,
        id_column: str,
        id_type: str,
        total: int,
        matched: int,
        unmatched: list,
        saved_cohort_id: str,
    ) -> str:
        unmatched_html = ""
        if unmatched:
            shown = unmatched[: self.valves.max_unmatched_to_show]
            extra = len(unmatched) - len(shown)
            items = "".join(f"<li>{html_module.escape(str(v))}</li>" for v in shown)
            extra_line = (
                f'<div class="unmatched-extra">… and {extra} more.</div>'
                if extra > 0
                else ""
            )
            unmatched_html = (
                f'<div class="unmatched">'
                f"<h4>Unmatched IDs ({len(unmatched)})</h4>"
                f"<ul>{items}</ul>{extra_line}</div>"
            )
        ridline = (
            f'<div class="rid">cohort_id: <code>{html_module.escape(saved_cohort_id)}</code></div>'
            if saved_cohort_id
            else ""
        )
        body = f"""
            <div class="card">
              <h3>{html_module.escape(source_file or 'Upload')}</h3>
              <div class="meta">column <code>{html_module.escape(id_column)}</code> · type <code>{html_module.escape(id_type)}</code></div>
              <div class="stats">
                <div class="stat"><div class="value">{total}</div><div class="label">total</div></div>
                <div class="stat ok"><div class="value">{matched}</div><div class="label">matched</div></div>
                <div class="stat warn"><div class="value">{len(unmatched)}</div><div class="label">unmatched</div></div>
              </div>
              {ridline}
              {unmatched_html}
            </div>
        """
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 13px; color: #e0e0e0; background: #1e1e1e; padding: 12px;
    }}
    .card {{ background: #2a2a2a; padding: 16px; border-radius: 8px; }}
    h3 {{ margin: 0 0 4px 0; font-size: 14px; }}
    .meta {{ color: #aaa; font-size: 12px; margin-bottom: 12px; }}
    .meta code {{ background: #1e1e1e; padding: 1px 5px; border-radius: 3px; }}
    .stats {{ display: flex; gap: 12px; margin-bottom: 12px; }}
    .stat {{ background: #1e1e1e; padding: 12px 16px; border-radius: 6px; min-width: 100px; }}
    .stat .value {{ font-size: 22px; font-weight: 700; color: #60a5fa; }}
    .stat .label {{ font-size: 11px; color: #aaa; text-transform: uppercase; letter-spacing: 0.04em; }}
    .stat.ok .value {{ color: #86efac; }}
    .stat.warn .value {{ color: #fda4af; }}
    .rid {{ font-size: 12px; color: #ccc; margin-bottom: 12px; }}
    .rid code {{ background: #1e1e1e; padding: 1px 5px; border-radius: 3px; color: #60a5fa; }}
    .unmatched h4 {{ font-size: 12px; color: #fda4af; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.04em; }}
    .unmatched ul {{ list-style: none; max-height: 220px; overflow: auto; background: #1e1e1e; padding: 8px 12px; border-radius: 4px; font-family: ui-monospace, monospace; font-size: 12px; color: #ccc; }}
    .unmatched-extra {{ color: #888; font-size: 11px; padding: 6px 12px; }}
</style>
</head>
<body>
{body}
<script>
    function reportHeight() {{
        const h = document.documentElement.scrollHeight;
        parent.postMessage({{ type: "iframe:height", height: Math.min(h, 800) }}, "*");
    }}
    reportHeight();
    new MutationObserver(reportHeight).observe(document.body, {{ childList: true, subtree: true }});
</script>
</body>
</html>"""

    @staticmethod
    async def _emit_status(
        emitter: Optional[Callable], description: str, done: bool
    ) -> None:
        if emitter:
            await emitter(
                {"type": "status", "data": {"description": description, "done": done}}
            )
