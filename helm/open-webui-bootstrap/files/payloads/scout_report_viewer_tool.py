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

import asyncio
import inspect
import json
import logging
import os
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Literal, Optional

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

_MAX_GET_IDS = 100
_MD_CELL_MAX = 400
_MAX_UPLOAD_BYTES = 32 * 1024 * 1024
EmbedKind = Literal["cohort", "chart"]
_EMBED_COHORT: EmbedKind = "cohort"
_EMBED_CHART: EmbedKind = "chart"
_MAX_CHARTS_PER_TURN = 4
_MAX_TURNS_TRACKED = 64
# turn key -> {"embeds": [(kind, url)], "lock": asyncio.Lock()}, LRU by write.
_TURN_EMBEDS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_SESSION_EXPIRED_MESSAGE = "Session expired - sign out of Open WebUI and back in, then regenerate this response."
_VIEWER_NOTE = (
    "The sample table above is a subset of results; when the search "
    "used match_terms or match_diagnoses, an evidence table with "
    "excerpts and matched diagnoses is included too. "
    "The full results are shown to the user in a search viewer above "
    "this message, alongside any charts you drew this turn. "
    "The user can sort, filter, and explore the full results there. "
    "Call this tool at most once per turn; a second call replaces this "
    "viewer. "
    "Do not restate the tables or the SQL. "
    "Use the sample and evidence to confirm your query and reply "
    "with insights, follow-up queries, pattern observations, "
    "refinement suggestions, etc."
)


class Tools:
    """Thin client over the Scout report-viewer service. Exposes
    LLM-callable methods: `scout_find_reports`, `scout_chart_sql`,
    `scout_query_sql`, `scout_get_reports`, `scout_get_chart_data`,
    all namespaced `scout_*` to disambiguate from OWUI built-ins
    (search_notes, view_note, etc.). See each method's docstring for
    its contract."""

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
        __message_id__: Optional[str] = None,
    ) -> Any:
        """Save a SQL search over Scout's radiology reports and render
        the results in a viewer above your reply.

        Call this at most once per turn: a second call replaces the first
        viewer. Charts from `scout_chart_sql` in the same turn are
        unaffected and stay on screen next to it.

        Two modes:
        * SQL mode: pass `sql` (and optional `match_terms`,
          `match_diagnoses`, `sql_explanation`). Every row must
          project `primary_report_identifier` and `accession_number`.
          Example:
              SELECT primary_report_identifier, accession_number,
                     epic_mrn, modality, service_name, message_dt,
                     patient_age, sex
              FROM reports_latest
              WHERE modality = 'CT'
        * File mode: pass `file_id` (uploaded CSV) and optionally
          `id_column` (one of `primary_report_identifier`,
          `accession_number`, `epic_mrn`, `patient_mpi`).
          When omitted, the backend infers the column from the header.
          Passing `sql` in file mode is optional: when set, it must
          include `{{cohort}}` exactly once and the backend substitutes
          the ID predicate. When omitted, a default projection is used.

        :param sql: SQL mode: full Trino query. File mode: optional
            custom SQL with `{{cohort}}` placeholder.
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
        :param id_column: File mode only. See allowed values above.
        :return: Markdown sample + evidence tables for your reasoning,
            plus the full-result viewer rendered for the user.
        """
        # File mode: delegate to the file-import branch. The LLM passes
        # file_id from `__files__[0].id`; the tool reads file bytes
        # server-side and the LLM context never sees them.
        if file_id:
            return await self._import_from_file(
                file_id=file_id,
                id_column=id_column,
                sql=sql,
                sql_explanation=sql_explanation,
                __event_emitter__=__event_emitter__,
                __oauth_token__=__oauth_token__,
                __metadata__=__metadata__,
                __message_id__=__message_id__,
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
            await self._emit(
                __event_emitter__, self._status_error(exc, "Search failed"), done=True
            )
            return self._error_text(exc, "Error fetching reports")

        count = created.get("count")
        if count == 0:
            await self._emit(__event_emitter__, "No matching reports", done=True)
            return (
                "No reports matched. Try scouting the data or broadening the criteria."
            )

        found = (
            f"Found {count:,} matching reports"
            if count is not None
            else "Found matching reports"
        )
        await self._emit(__event_emitter__, found, done=True)

        await self._emit_embed(
            __event_emitter__,
            created["view_url"],
            kind=_EMBED_COHORT,
            message_id=__message_id__,
            metadata=__metadata__,
        )

        return self._render_search_summary(created)

    async def scout_chart_sql(
        self,
        sql: str,
        vega_lite_spec: dict,
        sql_explanation: Optional[str] = None,
        file_id: Optional[str] = None,
        id_column: Optional[str] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __oauth_token__: Any = None,
        __metadata__: Optional[dict] = None,
        __message_id__: Optional[str] = None,
    ) -> str:
        """Chart the result of a query and show it to the user. Use whenever
        the user asks for a chart, plot, graph, distribution, trend,
        histogram, or breakdown.

        One chart per call. Several calls in one turn all render, and a
        chart can share a turn with a `scout_find_reports` viewer - prefer
        two focused charts over one crowded spec.

        Write the SQL and the Vega-Lite spec together in this one call, and
        **omit `data`** - the service runs the query and renders the chart
        itself, so neither the spec nor the rows come back to you.

        Always aggregate in the SQL so the query returns one row per mark -
        `GROUP BY`, bucketing ages or dates yourself. Reference columns by
        the names your SQL projects, e.g. `{"mark": "bar", "encoding":
        {"x": {"field": "age_bracket", "type": "ordinal"},
         "y": {"field": "patients", "type": "quantitative"}}}`.

        To chart a CSV cohort the user uploaded, pass `file_id` and include
        `{{cohort}}` exactly once in the SQL - the backend substitutes the ID
        predicate and stores the list with the chart, so it keeps working on
        later views. Never write the ID list out yourself.

        :param sql: Trino SQL for the chart's rows, already aggregated. In
            file mode, include `{{cohort}}` exactly once.
        :param vega_lite_spec: Vega-Lite spec with no `data` key.
        :param sql_explanation: One- to three-sentence plain-language
            description of what the chart shows, covering both the rows the
            SQL selects and what the chart does with them. Surfaced behind
            the viewer's "Explain Search" button so the user can sanity-check
            the chart without reading raw SQL.
        :param file_id: Optional. OWUI file id for a cohort CSV
            (typically `__files__[0].id`).
        :param id_column: Optional (file mode only). One of
            `primary_report_identifier`, `accession_number`, `epic_mrn`,
            `patient_mpi`. Inferred from the CSV header when omitted.
        :return: A one-line confirmation plus the chart's internal handle
            (keep it in mind for a later `scout_get_chart_data` call). The
            chart is rendered for the user above your message, next to
            anything else you rendered this turn; reply with
            interpretation only.
        """
        # Before the status emit, so a bad file reads as a file error.
        fetched: tuple[bytes, str] | None = None
        if file_id:
            got = await self._fetch_owui_file(file_id)
            if isinstance(got, str):
                return got
            fetched = got

        # A spec that fails to serialize would otherwise raise past every
        # handler below as a raw, unguided exception.
        try:
            json.dumps(vega_lite_spec)
        except (TypeError, ValueError, RecursionError) as exc:
            return (
                f"Error building chart: vega_lite_spec is not valid JSON ({exc}).\n\n"
                "Fix the spec and call scout_chart_sql again."
            )

        await self._emit(__event_emitter__, "Building chart\u2026", done=False)
        try:
            if fetched:
                plot = await self._chart_from_file(
                    fetched=fetched,
                    sql=sql,
                    vega_lite_spec=vega_lite_spec,
                    sql_explanation=sql_explanation,
                    id_column=id_column,
                    oauth=__oauth_token__,
                    metadata=__metadata__,
                )
            else:
                plot = await self._post(
                    "/api/plots",
                    {
                        "sql": sql,
                        "vega_lite_spec": vega_lite_spec,
                        "sql_explanation": sql_explanation or "",
                        "owui_chat_id": _chat_id(__metadata__),
                    },
                    oauth=__oauth_token__,
                )
        except ServiceTimeoutError:
            await self._emit(__event_emitter__, "Chart timed out", done=True)
            return (
                f"Chart query timed out after "
                f"{self.valves.request_timeout_seconds}s. "
                "The SQL is valid, it just scans too much."
            )
        except ReportViewerServiceError as exc:
            await self._emit(
                __event_emitter__, self._status_error(exc, "Chart failed"), done=True
            )
            error = self._error_text(exc, "Error building chart")
            if isinstance(exc, SessionExpiredError):
                return error
            return f"{error}\n\nFix the SQL or the spec and call scout_chart_sql again."
        n = plot.get("row_count", 0)
        await self._emit(__event_emitter__, "Chart ready", done=True)
        evicted = await self._emit_embed(
            __event_emitter__,
            plot["view_url"],
            kind=_EMBED_CHART,
            message_id=__message_id__,
            metadata=__metadata__,
        )
        rendered = (
            f"An earlier chart from this turn was dropped (max "
            f"{_MAX_CHARTS_PER_TURN} per turn) - this chart rendered for the "
            "user above this message"
            if evicted
            else "Chart rendered for the user above this message"
        )
        return (
            f"{rendered} ({n} data points over "
            f"{', '.join(plot.get('columns') or [])}). "
            "Do not restate the data, do not add a table, and do not write a "
            "vega code fence. Reply with a short interpretation only, in "
            "one reply covering every chart and viewer you rendered this "
            "turn.\n\n"
            f"Internal chart handle: {plot['id']}."
        )

    async def _chart_from_file(
        self,
        *,
        fetched: tuple[bytes, str],
        sql: str,
        vega_lite_spec: dict,
        sql_explanation: Optional[str],
        id_column: Optional[str],
        oauth: Any,
        metadata: Optional[dict],
    ) -> dict:
        """Chart scoped to an uploaded CSV cohort. `sql` must include
        `{{cohort}}` exactly once."""
        contents, filename = fetched
        form: dict[str, str] = {
            "sql": sql,
            "vega_lite_spec": json.dumps(vega_lite_spec),
        }
        if sql_explanation:
            form["sql_explanation"] = sql_explanation
        if id_column:
            form["id_column"] = id_column
        chat_id = _chat_id(metadata)
        if chat_id:
            form["owui_chat_id"] = chat_id
        return await self._post_multipart(
            "/api/plots/from-file",
            files={"file": (filename, contents, "text/csv")},
            data=form,
            oauth=oauth,
        )

    async def scout_get_chart_data(
        self,
        chart_id: str,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __oauth_token__: Any = None,
    ) -> Any:
        """Fetch the SQL, explanation, and rows behind a chart the user
        is already looking at, so you can analyze it in prose.

        Use whenever the user wants your read on a chart shown earlier
        in this conversation, whether or not they name it: patterns,
        outliers, follow-ups. If they name a chart's internal handle,
        use it; otherwise use the handle from the most recent
        `scout_chart_sql` call in this conversation. Unlike
        `scout_chart_sql`, which never returns its rows so a chart
        doesn't bloat every turn's context, this tool deliberately
        pulls them in for exactly this ask.

        :param chart_id: The chart's internal handle: the one the
            user named, or your most recent chart's if they didn't.
        :return: The chart's SQL, explanation, and rows as a markdown
            table, for you to interpret, not to restate as JSON or a
            fence.
        """
        await self._emit(__event_emitter__, "Reading chart data…", done=False)
        try:
            plot = await self._get(f"/api/plots/{chart_id}", oauth=__oauth_token__)
        except ReportViewerServiceError as exc:
            await self._emit(
                __event_emitter__,
                self._status_error(exc, "Could not read the chart"),
                done=True,
            )
            return self._error_text(exc, "Error reading chart")
        n = len(plot.get("rows") or [])
        await self._emit(__event_emitter__, f"Chart data ready ({n} rows)", done=True)
        return self._render_chart_data(plot)

    async def scout_query_sql(
        self,
        sql: str,
        file_id: Optional[str] = None,
        id_column: Optional[str] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __oauth_token__: Any = None,
    ) -> Any:
        """Run an ad-hoc SQL query and return rows inline.

        No search is persisted; no iframe is rendered.

        :param sql: Trino SQL. Default to `reports_latest`
            (`reports_curated` for report history); use an `_epic_view`
            only for patient-across-reports questions. When `file_id`
            is set, include `{{cohort}}` exactly once and the backend
            substitutes the CSV cohort predicate.
        :param file_id: Optional. OWUI file id for a cohort CSV.
        :param id_column: Optional (file mode only).
        :return: Markdown table of rows for direct inclusion in your
            prose reply.
        """
        if file_id:
            return await self._query_from_file(
                file_id=file_id,
                sql=sql,
                id_column=id_column,
                __event_emitter__=__event_emitter__,
                __oauth_token__=__oauth_token__,
            )
        await self._emit(__event_emitter__, "Running query…", done=False)
        try:
            agg = await self._post(
                "/api/reports/query", {"sql": sql}, oauth=__oauth_token__
            )
        except ReportViewerServiceError as exc:
            await self._emit(
                __event_emitter__, self._status_error(exc, "Query failed"), done=True
            )
            return self._error_text(exc, "Error running query")
        n = len(agg.get("rows", []))
        await self._emit(__event_emitter__, f"Query complete ({n} rows)", done=True)
        return self._format_aggregate(agg)

    async def _fetch_owui_file(self, file_id: str) -> tuple[bytes, str] | str:
        """Read an OWUI-uploaded file. Returns (contents, filename) or
        an error string suitable for the LLM."""
        try:
            from open_webui.models.files import Files
            from open_webui.storage.provider import Storage
        except Exception as exc:
            return f"Error: could not import OWUI file modules: {exc}"

        # OWUI 0.9.6 made get_file_by_id async; earlier versions were
        # sync. Await if we got a coroutine so this works on both.
        file_model = Files.get_file_by_id(file_id)
        if inspect.iscoroutine(file_model):
            file_model = await file_model
        if not file_model:
            return f"Error: file {file_id} not found in OWUI"
        file_path = getattr(file_model, "path", None)
        filename = getattr(file_model, "filename", None) or file_id
        if not file_path:
            return f"Error: file {file_id} has no readable path"
        # OWUI 0.9.6 returns a filesystem path; earlier versions returned
        # bytes or a file-like. Handle all three.
        try:
            got = Storage.get_file(file_path)
            if inspect.iscoroutine(got):
                got = await got
            if isinstance(got, str) and os.path.exists(got):
                if os.path.getsize(got) > _MAX_UPLOAD_BYTES:
                    return (
                        f"Error: {filename} exceeds "
                        f"{_MAX_UPLOAD_BYTES // (1024 * 1024)} MiB upload cap"
                    )
                with open(got, "rb") as _fh:
                    contents = _fh.read()
            elif hasattr(got, "read"):
                contents = got.read()
            else:
                contents = got
        except Exception as exc:
            return f"Error: could not read file {file_id}: {exc}"
        if not isinstance(contents, bytes):
            contents = str(contents).encode("utf-8")
        if len(contents) > _MAX_UPLOAD_BYTES:
            return (
                f"Error: {filename} exceeds "
                f"{_MAX_UPLOAD_BYTES // (1024 * 1024)} MiB upload cap"
            )
        return contents, filename

    async def _import_from_file(
        self,
        file_id: str,
        id_column: Optional[str] = None,
        sql: Optional[str] = None,
        sql_explanation: Optional[str] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __oauth_token__: Any = None,
        __metadata__: Optional[dict] = None,
        __message_id__: Optional[str] = None,
    ) -> Any:
        """Forward an OWUI-uploaded CSV to the report-viewer service,
        which parses, dedups, validates, and saves the search.

        :param file_id: OWUI file id (typically `__files__[0].id`).
        :param id_column: Optional. Backend infers from CSV header when
            omitted.
        :param sql: Optional custom SQL with `{{cohort}}` placeholder.
        :param sql_explanation: Optional. Surfaced in the SPA "About
            this search" panel.
        """
        fetched = await self._fetch_owui_file(file_id)
        if isinstance(fetched, str):
            return fetched
        contents, filename = fetched

        await self._emit(
            __event_emitter__,
            f"Uploading {filename}…",
            done=False,
        )
        form: dict[str, str] = {}
        if id_column:
            form["id_column"] = id_column
        if sql:
            form["sql"] = sql
        if sql_explanation:
            form["sql_explanation"] = sql_explanation
        chat_id = _chat_id(__metadata__)
        if chat_id:
            form["owui_chat_id"] = chat_id
        try:
            created = await self._post_multipart(
                "/api/searches/from-file",
                files={"file": (filename, contents, "text/csv")},
                data=form,
                oauth=__oauth_token__,
            )
        except ReportViewerServiceError as exc:
            await self._emit(
                __event_emitter__, self._status_error(exc, "Import failed"), done=True
            )
            return self._error_text(exc, "Error")
        count = created.get("count")
        if count == 0:
            await self._emit(__event_emitter__, "No matching reports", done=True)
            return "None of the IDs in your list matched. Try a different ID column."

        matched = (
            f"Matched {count:,} reports from your list"
            if count is not None
            else "Matched reports from your list"
        )
        await self._emit(__event_emitter__, matched, done=True)
        await self._emit_embed(
            __event_emitter__,
            created["view_url"],
            kind=_EMBED_COHORT,
            message_id=__message_id__,
            metadata=__metadata__,
        )
        return self._render_from_file_summary(created, filename)

    async def _query_from_file(
        self,
        file_id: str,
        sql: str,
        id_column: Optional[str] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __oauth_token__: Any = None,
    ) -> Any:
        """One-shot cohort-scoped query. `sql` must include `{{cohort}}`
        exactly once."""
        fetched = await self._fetch_owui_file(file_id)
        if isinstance(fetched, str):
            return fetched
        contents, filename = fetched

        await self._emit(__event_emitter__, f"Querying {filename}…", done=False)
        form: dict[str, str] = {"sql": sql}
        if id_column:
            form["id_column"] = id_column
        try:
            agg = await self._post_multipart(
                "/api/reports/query/from-file",
                files={"file": (filename, contents, "text/csv")},
                data=form,
                oauth=__oauth_token__,
            )
        except ReportViewerServiceError as exc:
            await self._emit(
                __event_emitter__, self._status_error(exc, "Query failed"), done=True
            )
            return self._error_text(exc, "Error running query")
        n = len(agg.get("rows", []))
        await self._emit(__event_emitter__, f"Query complete ({n} rows)", done=True)
        return self._format_aggregate(agg)

    async def scout_get_reports(
        self,
        ids: list[str],
        id_column: str = "primary_report_identifier",
        table: Optional[str] = None,
        __oauth_token__: Any = None,
    ) -> Any:
        """Fetch full report content (text, sections, diagnoses,
        metadata) by identifier.

        :param ids: Identifier list (max 100).
        :param id_column: Report-scoped (1 row each):
            `primary_report_identifier` (default), `accession_number`.
            Patient-scoped (all reports for that patient):
            `epic_mrn`, `patient_mpi`, `scout_patient_id`.
        :param table: Optional source table. Default `reports_curated`
            (all report versions, all patients). Pass an epic view
            (`reports_curated_epic_view` / `reports_latest_epic_view`)
            to resolve patient identity across HL7 versions; required
            for `id_column=scout_patient_id`. Epic views omit reports
            with an inconsistent patient graph.
        """
        if not ids:
            return "Error: ids must be a non-empty list."
        if len(ids) > _MAX_GET_IDS:
            return f"Error: at most {_MAX_GET_IDS} ids per call."
        payload: dict[str, Any] = {"ids": ids, "id_column": id_column}
        if table:
            payload["table"] = table
        try:
            result = await self._post(
                "/api/reports/read",
                payload,
                oauth=__oauth_token__,
            )
        except ReportViewerServiceError as exc:
            return self._error_text(exc, "Error reading reports")
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
        forwarding the caller's OWUI access token as Bearer. Raises
        SessionExpiredError if no token is available or report-viewer 401s
        it, or ReportViewerServiceError on any other 4xx/5xx from
        report-viewer."""
        url = f"{self.valves.report_viewer_internal_url.rstrip('/')}{path}"
        bearer = self._token_from_owui(oauth)
        if not bearer:
            raise SessionExpiredError(_SESSION_EXPIRED_MESSAGE)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bearer}",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.valves.request_timeout_seconds
            ) as c:
                r = await c.post(url, headers=headers, json=payload)
        except httpx.TimeoutException:
            raise ServiceTimeoutError("timed out")
        except httpx.RequestError:
            raise ReportViewerServiceError("report-viewer is temporarily unavailable")
        if r.status_code == 401:
            raise SessionExpiredError(_SESSION_EXPIRED_MESSAGE)
        if r.status_code >= 400:
            raise _service_error(r)
        return r.json()

    async def _get(self, path: str, *, oauth: Any) -> dict:
        """GET `report_viewer_internal_url + path`, forwarding the
        caller's OWUI access token as Bearer. Same error contract as
        `_post`."""
        url = f"{self.valves.report_viewer_internal_url.rstrip('/')}{path}"
        bearer = self._token_from_owui(oauth)
        if not bearer:
            raise SessionExpiredError(_SESSION_EXPIRED_MESSAGE)
        headers = {"Authorization": f"Bearer {bearer}"}
        try:
            async with httpx.AsyncClient(
                timeout=self.valves.request_timeout_seconds
            ) as c:
                r = await c.get(url, headers=headers)
        except httpx.TimeoutException:
            raise ServiceTimeoutError("timed out")
        except httpx.RequestError:
            raise ReportViewerServiceError("report-viewer is temporarily unavailable")
        if r.status_code == 401:
            raise SessionExpiredError(_SESSION_EXPIRED_MESSAGE)
        if r.status_code >= 400:
            raise _service_error(r)
        return r.json()

    async def _post_multipart(
        self,
        path: str,
        *,
        files: dict,
        data: dict,
        oauth: Any,
    ) -> dict:
        url = f"{self.valves.report_viewer_internal_url.rstrip('/')}{path}"
        bearer = self._token_from_owui(oauth)
        if not bearer:
            raise SessionExpiredError(_SESSION_EXPIRED_MESSAGE)
        headers = {"Authorization": f"Bearer {bearer}"}
        try:
            async with httpx.AsyncClient(
                timeout=self.valves.request_timeout_seconds
            ) as c:
                r = await c.post(url, headers=headers, files=files, data=data)
        except httpx.TimeoutException:
            raise ServiceTimeoutError("timed out")
        except httpx.RequestError:
            raise ReportViewerServiceError("report-viewer is temporarily unavailable")
        if r.status_code == 401:
            raise SessionExpiredError(_SESSION_EXPIRED_MESSAGE)
        if r.status_code >= 400:
            raise _service_error(r)
        return r.json()

    @staticmethod
    def _render_search_summary(created: dict) -> str:
        """Sample table + evidence table (omitted if every row's
        excerpt is null and matched_diagnoses is empty). Both keyed by
        id_column so they align visually."""
        count = created.get("count")
        columns: list[str] = created.get("columns") or []
        sample: list[dict] = created.get("sample") or []
        evidence: list[dict] = created.get("evidence") or []
        sid = created.get("id") or ""
        id_column = created.get("id_column") or ""

        cnt = f"{count:,}" if isinstance(count, int) else "an unknown number of"
        rows_word = "row" if count == 1 else "rows"
        parts = [f"SQL matched {cnt} {rows_word} across {len(columns)} columns."]

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
        parts.append(_VIEWER_NOTE)
        parts.append("")
        parts.append(f"Internal search handle: {sid}.")
        return "\n".join(parts)

    @staticmethod
    def _render_from_file_summary(created: dict, filename: str) -> str:
        count = created.get("count")
        columns: list[str] = created.get("columns") or []
        sample: list[dict] = created.get("sample") or []
        id_column = created.get("id_column") or "id"
        column_inferred = bool(created.get("column_inferred"))
        unmatched = list(created.get("unmatched") or [])
        unmatched_count = int(created.get("unmatched_count") or 0)
        sid = created.get("id") or ""

        cnt = f"{count:,}" if isinstance(count, int) else "an unknown number of"
        rows_word = "report" if count == 1 else "reports"
        parts = [f"Imported {cnt} {rows_word} from {filename} (keyed on {id_column})."]
        if column_inferred:
            parts.append(f"Inferred column: {id_column}.")
        if unmatched_count:
            unmatched_sample = ", ".join(unmatched)
            if unmatched_count > len(unmatched):
                parts.append(
                    f"{unmatched_count:,} IDs weren't found "
                    f"(showing {len(unmatched)}): {unmatched_sample}."
                )
            else:
                parts.append(
                    f"{unmatched_count:,} IDs weren't found: {unmatched_sample}."
                )
        if sample and columns:
            parts.append("")
            parts.extend(_md_table(columns, sample))
        parts.append("")
        parts.append(_VIEWER_NOTE)
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
    def _render_chart_data(plot: dict) -> str:
        """SQL + explanation + rows for a chart the user is already
        looking at, so the LLM can interpret it rather than re-render
        or restate it."""
        sql = plot.get("sql") or ""
        explanation = plot.get("sql_explanation") or ""
        rows: list[dict] = plot.get("rows") or []
        parts: list[str] = []
        if explanation:
            parts.append(explanation)
            parts.append("")
        parts.append(f"SQL:\n```sql\n{sql}\n```")
        if rows:
            parts.append("")
            parts.extend(_md_table(list(rows[0].keys()), rows))
        else:
            parts.append("")
            parts.append("Query returned no rows.")
        parts.append("")
        parts.append(
            "This is the data behind the chart the user is already looking at. "
            "Do not restate it as a table or JSON, and do not call "
            "scout_chart_sql again unless the user asks for a new or revised "
            "chart. Reply with your analysis: patterns, outliers, anything "
            "worth flagging."
        )
        return "\n".join(parts)

    @staticmethod
    def _error_text(exc: ReportViewerServiceError, prefix: str) -> str:
        """A SessionExpiredError message is already complete and
        user-facing; other failures get the caller's context prefix."""
        return str(exc) if isinstance(exc, SessionExpiredError) else f"{prefix}: {exc}"

    @staticmethod
    def _status_error(exc: ReportViewerServiceError, what: str) -> str:
        """Status-pill text. `what` is the fallback for failures only the
        model can act on; the full message still goes to it via the return."""
        if isinstance(exc, SessionExpiredError):
            return str(exc)
        if isinstance(exc, ServiceTimeoutError):
            return "Timed out, try a narrower query"
        if exc.status == 413 and exc.detail:
            return exc.detail  # already written for the user
        if exc.status == 404:
            return "Not found"
        if exc.status == 0 or exc.status >= 500:
            return "Scout is temporarily unavailable, try again in a moment"
        return what

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
        *,
        kind: EmbedKind,
        message_id: Any = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """Render this turn's viewers and charts in `message.embeds`. Returns
        whether an older chart was evicted to make room for this one.

        OWUI assigns `message.embeds = data.embeds` outright, so a bare
        single-URL emit wipes whatever an earlier tool call in the same turn
        rendered. The emitter is write-only, so the turn's list is tracked
        here and replayed whole on every call: charts accumulate, and a
        second cohort viewer supersedes the first.

        Without a message id one turn cannot be told from the next, so the
        URL is emitted alone and nothing is stored.
        """
        if emitter is None:
            return False
        key = _turn_key(message_id, metadata)
        if not key:
            await Tools._send_embeds(emitter, [url])
            return False
        entry, evicted = _record_embed(key, kind, url)
        # Held across the send so parallel tool calls in one turn cannot land
        # their snapshots out of order.
        async with entry["lock"]:
            await Tools._send_embeds(emitter, [u for _, u in entry["embeds"]])
        return evicted

    @staticmethod
    async def _send_embeds(
        emitter: Callable[[Any], Awaitable[None]], urls: list[str]
    ) -> None:
        """`replace` is inert in OWUI 0.11 (it always assigns) but states the
        intent if append semantics ever land."""
        try:
            await emitter(
                {
                    "type": "embeds",
                    "data": {"embeds": urls, "replace": True},
                }
            )
        except Exception:
            log.debug("embeds emit failed (non-fatal)", exc_info=True)


class ReportViewerServiceError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0, detail: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


class SessionExpiredError(ReportViewerServiceError):
    """No usable OWUI token: either none was provided, or report-viewer rejected it."""


class ServiceTimeoutError(ReportViewerServiceError):
    """The request outlived `request_timeout_seconds`. The query is valid, so
    this is a cost signal, not something to fix in the SQL."""


def _chat_id(meta: Any) -> str:
    """Pull `chat_id` out of OWUI's `__metadata__` so the SPA can group
    searches by conversation. Falls back to empty string if absent."""
    if isinstance(meta, dict):
        return str(meta.get("chat_id") or "")
    return ""


def _turn_key(message_id: Any, meta: Any) -> str:
    """Identity of the assistant message being built. OWUI injects
    `__message_id__` flat; older builds carry it only in `__metadata__`.
    `chat_id` namespaces it so ids cannot collide across chats. Empty means
    no turn identity: keying on chat_id or session_id alone would replay one
    turn's embeds into the next."""
    mid = str(message_id or "")
    if not mid and isinstance(meta, dict):
        mid = str(meta.get("message_id") or meta.get("assistant_message_id") or "")
    return f"{_chat_id(meta)}:{mid}" if mid else ""


def _next_embeds(
    prev: list[tuple[str, str]], kind: str, url: str
) -> tuple[list[tuple[str, str]], bool]:
    """Fold one embed into a turn's list. Charts accumulate in emission
    order; at most one cohort viewer survives, and a newer one lands last.
    Over the cap the oldest chart is dropped, never the cohort."""
    out = [
        e
        for e in prev
        if e != (kind, url) and not (kind == _EMBED_COHORT and e[0] == _EMBED_COHORT)
    ]
    out.append((kind, url))
    charts = [i for i, e in enumerate(out) if e[0] == _EMBED_CHART]
    dropped = charts[: max(0, len(charts) - _MAX_CHARTS_PER_TURN)]
    for i in reversed(dropped):
        del out[i]
    return out, bool(dropped)


def _record_embed(key: str, kind: str, url: str) -> tuple[dict[str, Any], bool]:
    """Fold `url` into the turn's entry, LRU-evicting stale turns so a
    long-lived process cannot accumulate one entry per message."""
    entry = _TURN_EMBEDS.get(key)
    if entry is None:
        entry = {"embeds": [], "lock": asyncio.Lock()}
        _TURN_EMBEDS[key] = entry
    entry["embeds"], evicted = _next_embeds(entry["embeds"], kind, url)
    _TURN_EMBEDS.move_to_end(key)
    while len(_TURN_EMBEDS) > _MAX_TURNS_TRACKED:
        _TURN_EMBEDS.popitem(last=False)
    return entry, evicted


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


def _service_error(resp: httpx.Response) -> ReportViewerServiceError:
    """Message for the model; status and detail kept for the status pill."""
    detail = ""
    try:
        body = resp.json()
        if isinstance(body, dict) and body.get("detail"):
            detail = str(body["detail"])
    except Exception:
        pass
    return ReportViewerServiceError(
        f"{resp.status_code}: {detail or resp.text[:200]}",
        status=resp.status_code,
        detail=detail,
    )
