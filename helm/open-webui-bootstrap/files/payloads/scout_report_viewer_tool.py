"""
title: Scout Report Viewer Tool
description: Save search SQL with the Scout report-viewer and surface
             the result in chat as a sample + evidence table for the
             LLM plus an iframe of the viewer for the user. Rows are
             evaluated on demand by the service (no materialization).
author: Scout Team
version: 0.1.0
"""

from __future__ import annotations

import csv
import inspect
import io
import json
import logging
import os
import re
from typing import Any, Awaitable, Callable, Optional

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

_MAX_GET_IDS = 100
_MD_CELL_MAX = 400


class Tools:
    """Thin client over the Scout report-viewer service. Exposes three
    LLM-callable methods - `scout_find_reports`, `scout_query_sql`,
    `scout_get_reports` - all namespaced `scout_*` to disambiguate
    from OWUI built-ins (search_notes, view_note, etc.). See each
    method's docstring for its contract."""

    class Valves(BaseModel):
        report_viewer_internal_url: str = Field(
            default="http://report-viewer.scout-analytics:8000",
            description=(
                "In-cluster base URL of the report-viewer. The tool "
                "POSTs SQL here and embeds the public `view_url` it "
                "returns into the chat message."
            ),
        )
        request_timeout_seconds: int = Field(default=120, ge=10, le=600)

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def scout_find_reports(
        self,
        sql: Optional[str] = None,
        match_terms: Optional[list[str]] = None,
        match_diagnoses: Optional[list[str]] = None,
        sql_explanation: Optional[str] = None,
        file_id: Optional[str] = None,
        id_column: Optional[str] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __oauth_token__: Any = None,
        __metadata__: Optional[dict] = None,
    ) -> Any:
        """Save a SQL search and render the viewer iframe.

        Two modes:
        * SQL mode: pass `sql` (and optional `match_terms`,
          `match_diagnoses`, `sql_explanation`). The SELECT must
          project one of the known identifier columns
          (`primary_report_identifier`, `accession_number`,
          `message_control_id`, `epic_mrn`, `mpi`, `scout_patient_id`).
        * File mode: pass `file_id` and `id_column`. The tool reads
          the file server-side - your context never sees its bytes.

        :param sql: Trino SQL against `delta.default.reports_latest`
            or `_epic_view`. Each saved search is standalone, no
            placeholder substitution.
        :param match_terms: Clinical text terms. Populates the
            `excerpt` field on each evidence row and highlights the
            terms in the row-expand viewer.
        :param match_diagnoses: ICD codes or code prefixes (e.g.
            `R91`, `R91.1`, `J18%`). Populates `matched_diagnoses` on
            each evidence row and lights up matching chips in the
            row-expand viewer.
        :param sql_explanation: One- to three-sentence plain-language
            description of what the SQL matches. Surfaced in the
            "About this search" panel for the user.
        :param file_id: OWUI file id (file mode only).
        :param id_column: Which column the file's IDs map to (file
            mode only).
        :return: Markdown sample + evidence tables for your reasoning,
            plus an embedded `<iframe>` of the viewer for the user.
        """
        # File mode: delegate to the file-import branch. The LLM passes
        # file_id from `__files__[0].id`; the tool reads file bytes
        # server-side and the LLM context never sees them.
        if file_id:
            return await self._import_from_file(
                file_id=file_id,
                id_column=id_column or "accession_number",
                __event_emitter__=__event_emitter__,
                __oauth_token__=__oauth_token__,
                __metadata__=__metadata__,
            )

        if not sql:
            return "Error: scout_find_reports requires either `sql` or `file_id`."

        payload: dict[str, Any] = {"sql": sql}
        if match_terms:
            payload["match_terms"] = match_terms
        if match_diagnoses:
            payload["match_diagnoses"] = match_diagnoses
        if sql_explanation:
            payload["sql_explanation"] = sql_explanation
        chat_id = _chat_id(__metadata__)
        if chat_id:
            payload["owui_chat_id"] = chat_id

        await self._emit(__event_emitter__, "Searching reports…", done=False)
        try:
            created = await self._post("/api/searches", payload, oauth=__oauth_token__)
        except ReportViewerServiceError as exc:
            await self._emit(__event_emitter__, f"Failed: {exc}", done=True)
            return f"Error fetching reports: {exc}"

        await self._emit(
            __event_emitter__,
            f"Found {created['count']:,} matching reports",
            done=True,
        )

        # `replace: true` keeps a single iframe per message even if the
        # LLM iterates scout_find_reports mid-turn. embeds is separate
        # from message.content, working around OWUI 0.9.5's outlet-filter
        # + message-event injection gaps.
        await self._emit_embed(__event_emitter__, created["view_url"])

        return self._render_search_summary(created)

    async def scout_query_sql(
        self,
        sql: str,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __oauth_token__: Any = None,
    ) -> Any:
        """Run an ad-hoc SQL query and return rows inline.

        No search is persisted; no iframe is rendered.

        :param sql: Trino SQL against `delta.default.reports_latest`
            or `_epic_view`.
        :return: Markdown table of rows for direct inclusion in your
            prose reply.
        """
        await self._emit(__event_emitter__, "Running query…", done=False)
        try:
            agg = await self._post(
                "/api/reports/query", {"sql": sql}, oauth=__oauth_token__
            )
        except ReportViewerServiceError as exc:
            await self._emit(__event_emitter__, f"Failed: {exc}", done=True)
            return f"Error running query: {exc}"
        n = len(agg.get("rows", []))
        await self._emit(__event_emitter__, f"Query complete ({n} rows)", done=True)
        return self._format_aggregate(agg)

    async def _import_from_file(
        self,
        file_id: str,
        id_column: str = "accession_number",
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __oauth_token__: Any = None,
        __metadata__: Optional[dict] = None,
    ) -> Any:
        """Import an external ID list as a search. Tool reads the file
        server-side; the LLM context never sees its bytes.

        :param file_id: OWUI file id (typically `__files__[0].id`).
        :param id_column: Column the IDs map to.
        :return: Markdown with matched/unmatched counts plus an iframe
            of the resulting search.
        """
        # Read the file out of OWUI's local storage via the in-process
        # OWUI Python API (the tool runs inside the OWUI worker).
        try:
            from open_webui.models.files import Files
            from open_webui.storage.provider import Storage
        except Exception as exc:
            return f"Error: could not import OWUI file modules: {exc}"

        # OWUI 0.9.x has both sync and async signatures for these
        # depending on minor version; await if it returns a coroutine
        # so we work on both. (Real bug: 0.9.6 made get_file_by_id
        # async, which made the sync caller blow up with
        # "'coroutine' object has no attribute 'path'" on file_model.path.)
        file_model = Files.get_file_by_id(file_id)
        if inspect.iscoroutine(file_model):
            file_model = await file_model
        if not file_model:
            return f"Error: file {file_id} not found in OWUI"
        # OWUI 0.9.6 changed `Storage.get_file()` to return the local
        # filesystem path of the upload, not the file contents.
        # (Earlier versions returned bytes / a file-like object.) Handle
        # all three shapes: str path → open ourselves; bytes/str data →
        # use directly; file-like → .read().
        try:
            got = Storage.get_file(file_model.path)
            if inspect.iscoroutine(got):
                got = await got
            if isinstance(got, str) and os.path.exists(got):
                with open(got, "rb") as _fh:
                    contents = _fh.read()
            elif hasattr(got, "read"):
                contents = got.read()
            else:
                contents = got
        except Exception as exc:
            return f"Error: could not read file {file_id}: {exc}"
        if isinstance(contents, bytes):
            try:
                text = contents.decode("utf-8")
            except UnicodeDecodeError:
                text = contents.decode("latin-1", errors="replace")
        else:
            text = str(contents)

        # Parse IDs. Accepts:
        #   * one-id-per-line plain text
        #   * CSV/TSV with a recognized id-column header
        # Picks the id_column header automatically if present, else
        # uses the first column.
        ids: list[str] = []
        try:
            reader = csv.reader(io.StringIO(text))
            rows = list(reader)
        except Exception:
            rows = [[line] for line in text.splitlines() if line.strip()]

        if rows:
            header = rows[0]
            looks_like_header = any(
                re.search(r"[a-zA-Z]", c) and not re.fullmatch(r"\d[\d.-]*", c.strip())
                for c in header
            )
            if looks_like_header:
                # Find the column by exact match first, then by substring.
                target_idx = None
                lowered = [c.strip().lower() for c in header]
                want = id_column.lower()
                if want in lowered:
                    target_idx = lowered.index(want)
                else:
                    aliases = {
                        "accession_number": ["accession", "acc", "acc_num"],
                        "epic_mrn": ["mrn", "epic_mrn", "epicmrn", "patient_mrn"],
                        "message_control_id": ["message_control_id", "msg_id", "mcid"],
                    }.get(want, [])
                    for i, h in enumerate(lowered):
                        if any(a in h for a in aliases):
                            target_idx = i
                            break
                if target_idx is None:
                    target_idx = 0  # fall back to first column
                for r in rows[1:]:
                    if target_idx < len(r):
                        v = r[target_idx].strip()
                        if v:
                            ids.append(v)
            else:
                # No header - every row is just an id in column 0.
                for r in rows:
                    if r and r[0].strip():
                        ids.append(r[0].strip())

        if not ids:
            # Debug aid: surface what we actually saw so we can tell whether
            # the file read came back empty vs the header didn't match.
            preview = (text or "")[:200].replace("\n", "\\n")
            return (
                f"Error: could not extract any IDs from "
                f"{file_model.filename}. text_len={len(text or '')}, "
                f"rows_parsed={len(rows)}, preview={preview!r}"
            )

        await self._emit(
            __event_emitter__,
            f"Validating {len(ids)} IDs from {file_model.filename}…",
            done=False,
        )
        payload = {
            "ids": ids,
            "id_column": id_column,
            "sql_explanation": (
                f"Imported {len(ids)} {id_column} values from "
                f"{file_model.filename}."
            ),
            "owui_chat_id": _chat_id(__metadata__),
        }
        try:
            created = await self._post(
                "/api/searches/from-file", payload, oauth=__oauth_token__
            )
        except ReportViewerServiceError as exc:
            await self._emit(__event_emitter__, f"Import failed: {exc}", done=True)
            return f"Error: {exc}"
        view_url = created["view_url"]
        await self._emit(
            __event_emitter__,
            f"Matched {created['count']:,} reports from your ID list",
            done=True,
        )
        await self._emit_embed(__event_emitter__, view_url)
        return self._render_from_file_summary(created)

    async def scout_get_reports(
        self,
        ids: list[str],
        id_column: str = "primary_report_identifier",
        __oauth_token__: Any = None,
    ) -> Any:
        """Fetch full report content (text, sections, diagnoses,
        metadata) by identifier.

        :param ids: Identifier list (max 100).
        :param id_column: Report-scoped (1 row each):
            `primary_report_identifier` (default), `accession_number`,
            `message_control_id`. Patient-scoped (all reports for
            that patient): `epic_mrn`, `mpi`, `scout_patient_id`.
        """
        if not ids:
            return "Error: ids must be a non-empty list."
        if len(ids) > _MAX_GET_IDS:
            return f"Error: at most {_MAX_GET_IDS} ids per call."
        try:
            result = await self._post(
                "/api/reports/read",
                {"ids": ids, "id_column": id_column},
                oauth=__oauth_token__,
            )
        except ReportViewerServiceError as exc:
            return f"Error reading reports: {exc}"
        return json.dumps(result, default=str, indent=2)

    @staticmethod
    def _token_from_owui(oauth: Any) -> Optional[str]:
        """OWUI passes `__oauth_token__` as either a string access_token
        or a dict like `{access_token, refresh_token, ...}`. Normalize."""
        if not oauth:
            return None
        if isinstance(oauth, dict):
            return oauth.get("access_token") or None
        if isinstance(oauth, str):
            return oauth
        return None

    async def _post(self, path: str, payload: dict, *, oauth: Any) -> dict:
        """POST `payload` as JSON to `report_viewer_internal_url + path`,
        forwarding the caller's OWUI access token as Bearer if present.
        Raises ReportViewerServiceError on any 4xx/5xx."""
        url = f"{self.valves.report_viewer_internal_url.rstrip('/')}{path}"
        headers = {"Content-Type": "application/json"}
        bearer = self._token_from_owui(oauth)
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        async with httpx.AsyncClient(timeout=self.valves.request_timeout_seconds) as c:
            r = await c.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise ReportViewerServiceError(_short_error(r))
        return r.json()

    @staticmethod
    def _render_search_summary(created: dict) -> str:
        """Sample table + evidence table (omitted if every row's
        excerpt is null and matched_diagnoses is empty). Both keyed by
        id_column so they align visually."""
        count = int(created.get("count") or 0)
        columns: list[str] = created.get("columns") or []
        sample: list[dict] = created.get("sample") or []
        evidence: list[dict] = created.get("evidence") or []
        sid = created.get("id") or ""
        id_column = created.get("id_column") or ""

        rows_word = "row" if count == 1 else "rows"
        parts = [f"SQL matched {count:,} {rows_word} across {len(columns)} columns."]

        if sample and columns:
            parts.append("")
            parts.extend(_md_table(columns, sample))

        ev_rows: list[dict] = []
        for ev in evidence:
            excerpt = ev.get("excerpt")
            mdx = ev.get("matched_diagnoses") or []
            if not excerpt and not mdx:
                continue
            ev_rows.append(
                {
                    id_column: ev.get(id_column, ""),
                    "excerpt": excerpt or "",
                    "matched diagnoses": "; ".join(
                        f"{d.get('code', '')} ({d.get('text', '')})" for d in mdx
                    ),
                }
            )
        if ev_rows:
            parts.append("")
            parts.extend(
                _md_table([id_column, "excerpt", "matched diagnoses"], ev_rows)
            )

        parts.append("")
        parts.append(f"Internal search handle: {sid}.")
        return "\n".join(parts)

    @staticmethod
    def _render_from_file_summary(created: dict) -> str:
        """Matched/submitted/unmatched counts. No sample or evidence
        on this path (file mode takes no match_terms / match_diagnoses)."""
        count = int(created.get("count") or 0)
        submitted = int(created.get("submitted_count") or 0)
        unmatched_total = int(created.get("unmatched_total") or 0)
        id_column = created.get("id_column") or "id"
        sid = created.get("id") or ""

        parts = [f"Imported {count:,} of {submitted:,} submitted {id_column} values."]
        if unmatched_total:
            parts.append(
                f"{unmatched_total:,} IDs were not found in reports_latest "
                f"and were dropped."
            )
        parts.append("")
        parts.append(f"Internal search handle: {sid}.")
        return "\n".join(parts)

    @staticmethod
    def _format_aggregate(agg: dict) -> str:
        """Render an aggregate result as a small markdown table so the
        LLM can drop it straight into its prose reply. Service returns
        `{columns, rows}`. Empty result → a literal "no rows" marker the
        LLM can phrase around."""
        cols: list[str] = agg.get("columns") or []
        rows: list[dict] = agg.get("rows") or []
        if not rows:
            return "Aggregate query returned no rows."
        return "\n".join(_md_table(cols, rows))

    @staticmethod
    async def _emit(
        emitter: Optional[Callable[[Any], Awaitable[None]]],
        text: str,
        *,
        done: bool,
    ) -> None:
        if emitter is None:
            return
        try:
            await emitter(
                {
                    "type": "status",
                    "data": {"description": text, "done": done},
                }
            )
        except Exception:
            log.debug("status emit failed (non-fatal)", exc_info=True)

    @staticmethod
    async def _emit_embed(
        emitter: Optional[Callable[[Any], Awaitable[None]]],
        url: str,
    ) -> None:
        """Push a single iframe URL into `message.embeds`. `replace: True`
        wipes any prior embeds on the same message, so iterative
        scout_find_reports calls within one turn don't stack iframes."""
        if emitter is None:
            return
        try:
            await emitter(
                {
                    "type": "embeds",
                    "data": {"embeds": [url], "replace": True},
                }
            )
        except Exception:
            log.debug("embeds emit failed (non-fatal)", exc_info=True)


class ReportViewerServiceError(RuntimeError):
    pass


def _chat_id(meta: Any) -> str:
    """Pull `chat_id` out of OWUI's `__metadata__` so the SPA can group
    searches by conversation. Falls back to empty string if absent."""
    if isinstance(meta, dict):
        return str(meta.get("chat_id") or "")
    return ""


def _md_table(columns: list[str], rows: list[dict]) -> list[str]:
    """Markdown table lines. Cells truncate at _MD_CELL_MAX to bound
    chat context against a runaway projection; under typical use the
    excerpt + projection columns are well under the cap."""
    header = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    out = [header, sep]
    for r in rows:
        cells = []
        for c in columns:
            raw = r.get(c)
            v = "" if raw is None else str(raw)
            v = v.replace("|", "\\|").replace("\n", " ")
            if len(v) > _MD_CELL_MAX:
                v = v[: _MD_CELL_MAX - 1] + "…"
            cells.append(v)
        out.append("| " + " | ".join(cells) + " |")
    return out


def _short_error(resp: httpx.Response) -> str:
    """Trim the response body for an LLM-readable error string."""
    try:
        body = resp.json()
        detail = body.get("detail") if isinstance(body, dict) else None
        if detail:
            return f"{resp.status_code}: {detail}"
    except Exception:
        pass
    return f"{resp.status_code}: {resp.text[:200]}"
