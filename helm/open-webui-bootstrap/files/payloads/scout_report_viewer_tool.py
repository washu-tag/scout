"""
title: Scout Report Viewer Tool
description: Save search SQL with the Scout report-viewer and surface
             the search in chat as a small text summary + an iframe
             linking to the viewer. Rows are evaluated on demand by the
             service (no row materialization); chat carries only the
             search_id + view URL.
author: Scout Team
version: 0.1.0
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Awaitable, Callable, Optional

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


class Tools:
    """LLM-callable functions backed by the report-viewer. All
    methods are namespaced `scout_*` to disambiguate from OWUI
    built-ins (search_notes, view_note, search_chats, etc.).

      * `scout_find_reports(sql, highlight_terms?, sql_explanation?)`
        — POST /api/searches, saves the SQL, returns a short summary
        for the LLM plus a viewer iframe for the user. For row-level
        discovery. When refining, write a NEW call with the original
        conditions plus the new constraint; the SQL is standalone, no
        placeholder substitution.

      * `scout_query_sql(sql)` — POST /api/reports/query, runs SQL and
        returns rows directly to LLM context. No persistence, no
        iframe. For aggregate questions (COUNT, GROUP BY, time-series).

      * `scout_get_reports(ids, id_column)` — POST /api/reports/read,
        direct fetch of full report content by identifier. Use when
        you have a lake file path (default `id_column`), accession
        number, MRN, etc. and want the actual report. No prior search
        needed.
    """

    class Valves(BaseModel):
        report_viewer_internal_url: str = Field(
            default="http://report-viewer.scout-analytics:8000",
            description=(
                "In-cluster base URL of the report-viewer. The tool "
                "POSTs SQL here and embeds the public `view_url` it "
                "returns into the chat message."
            ),
        )
        iframe_height_px: int = Field(default=500, ge=200, le=1200)
        request_timeout_seconds: int = Field(default=120, ge=10, le=600)

    def __init__(self) -> None:
        self.valves = self.Valves()

    # ------------------------------------------------------------------
    # scout_find_reports — build a search from a SQL query
    # ------------------------------------------------------------------
    async def scout_find_reports(
        self,
        sql: Optional[str] = None,
        highlight_terms: Optional[list[str]] = None,
        highlight_diagnosis: Optional[list[str]] = None,
        sql_explanation: Optional[str] = None,
        llm_context_rows: Optional[int] = None,
        file_id: Optional[str] = None,
        id_column: Optional[str] = None,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __oauth_token__: Any = None,
        __metadata__: Optional[dict] = None,
    ) -> Any:
        """
        Build a saved search. Two input modes:

        * **SQL mode (default)**: pass `sql` (and `highlight_terms`,
          `sql_explanation`). The tool POSTs the SQL to `/api/searches`.
        * **File mode**: pass `file_id` (OWUI file ID from the user's
          attachment) and `id_column`. The tool reads the file
          server-side, validates IDs against `reports_latest`, and saves
          a `WHERE <id_col> IN (...)` SQL. Do NOT read file contents
          into your context — that's what the file mode is for.

        Saves the search SQL,
        renders the search viewer iframe in chat, and returns a rich
        summary (count + columns + sample table + anti-restatement
        directive) to your context. Full report text never enters your
        context — the user sees all rows in the iframe and can click
        any row to expand into the full report (with highlight terms
        emphasized) on demand.

        Use for search building (row-level discovery). For aggregate
        questions (COUNT, GROUP BY, time-series — anything where the
        answer is a number or a small summary table) use
        `scout_query_sql` instead — it doesn't persist anything and
        returns rows directly to your context.

        Refinement: when the user asks to narrow the search ("only MRs",
        "drop the under-18 patients"), write a NEW `scout_find_reports`
        call with the original conditions plus the new constraint.
        Each search is a standalone saved query — there is no
        placeholder substitution or parent-search SQL injection. The
        SPA homepage groups the chain by chat for the user's benefit.

        Your SELECT must include one of `message_control_id`,
        `accession_number`, or `epic_mrn`. The canonical row-level
        SELECT is `message_control_id, accession_number, modality,
        service_name, message_dt, patient_age, sex`. The SPA fetches
        full report text + diagnoses on row-expand (no need to include
        them in your SELECT). Prefer specific projections over `SELECT *`.

        :param sql: Trino SQL against `delta.default.reports_latest`
            (or `_epic_view`). Each search SQL stands alone — no
            placeholder substitution.
        :param highlight_terms: Clinical text terms (max ~5) for the
            row-expand viewer to highlight in the report text. Matched
            with word boundaries on the SPA side. Good values:
            `["pulmonary embolism", "PE"]`, `["cerebral infarction"]`,
            `["glioblastoma", "GBM"]`. BAD values: `["brain"]`,
            `["chest"]`, `["MRI"]` — those belong in the SQL itself.
            UI-only: highlighting does NOT filter rows. Without
            highlight_terms, the LLM-bound sample loses the `snippet`
            field that shows ±80 chars around each match.
        :param highlight_diagnosis: ICD codes or code prefixes (max
            ~5) that drove this cohort. Examples: `["R91.1"]`,
            `["R91"]` (prefix matches R91.1, R91.8, ...), `["J18%"]`
            (SQL-LIKE prefix, treated the same). Matched against
            `diagnosis_code` with case-insensitive startswith. Use
            when the SQL filters on diagnosis codes (e.g.
            `any_match(diagnoses, x -> x.diagnosis_code LIKE 'R91%')`)
            so the LLM-bound sample's `positive_dx` field — and the
            chip-row in the row-expand viewer — flag the matching
            codes explicitly. Distinct from `highlight_terms`; use
            both when the cohort is both text-driven and code-driven.
        :param sql_explanation: One- to three-sentence plain-language
            description of what the SQL matches and why. Surfaced in
            the SPA's "About this search" panel so the user can
            sanity-check what was built without reading raw SQL.
            Example: "Patients with chest CT reports from 2024+ that
            mention pulmonary nodules in the impression or findings
            section, excluding any negated mentions like 'no nodule'.
            ICD-coded R91% diagnoses are also included regardless of
            text negation." Required on every search.
        :param llm_context_rows: Sample-row cap. Default 5.
        :return: Rich text summary + an embedded `<iframe>` of the
            viewer. Service renders the summary; tool just forwards it.
        """
        # File mode: delegate to the file-import branch. The LLM passes
        # file_id from `__files__[0].id`; the tool reads file bytes
        # server-side and the LLM context never sees them.
        if file_id:
            return await self._import_from_file(
                file_id=file_id,
                id_column=id_column or "accession_number",
                __user__=__user__,
                __event_emitter__=__event_emitter__,
                __oauth_token__=__oauth_token__,
                __metadata__=__metadata__,
            )

        if not sql:
            return "Error: scout_find_reports requires either `sql` or `file_id`."

        bearer = await self._bearer_for_outbound(__oauth_token__)

        await self._emit(__event_emitter__, "Searching reports…", done=False)
        try:
            # OWUI injects chat metadata into the tool call. Pull the
            # chat ID so the SPA homepage can group searches by
            # conversation.
            chat_id = ""
            if isinstance(__metadata__, dict):
                chat_id = str(__metadata__.get("chat_id") or "")

            created = await self._post_search(
                sql,
                bearer=bearer,
                highlight_terms=highlight_terms,
                highlight_diagnosis=highlight_diagnosis,
                sql_explanation=sql_explanation,
                llm_context_rows=llm_context_rows,
                owui_chat_id=chat_id,
            )
        except ReportViewerServiceError as exc:
            await self._emit(__event_emitter__, f"Failed: {exc}", done=True)
            return f"Error fetching reports: {exc}"

        search_id = created["id"]
        count = created["count"]
        view_url = created["view_url"]

        await self._emit(
            __event_emitter__, f"Found {count:,} matching reports", done=True
        )

        # Push the viewer URL into OWUI's `message.embeds` field —
        # scout_find_reports always saves the search + renders the
        # iframe. Aggregate / GROUP-BY questions use `scout_query_sql`
        # instead (ephemeral, no iframe).
        #
        # `replace: true` keeps a single iframe per message even if
        # the LLM iterates scout_find_reports mid-turn. The embeds
        # field is separate from message.content so this works
        # around OWUI 0.9.5's outlet-filter + message-event injection
        # gaps.
        await self._emit_embed(__event_emitter__, view_url)

        # The service returns the rich LLM-bound markdown summary
        # (count + columns + USER DISPLAY directive + sample table
        # + internal search handle). Return it verbatim — the LLM uses
        # it for its reply; the user sees the iframe via the embeds
        # field above.
        return created.get("summary") or ""

    # ------------------------------------------------------------------
    # scout_query_sql — ad-hoc SQL, ephemeral, returns rows to LLM
    # ------------------------------------------------------------------
    async def scout_query_sql(
        self,
        sql: str,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __oauth_token__: Any = None,
    ) -> Any:
        """
        Run an ad-hoc SQL query and return rows directly to your
        context. No search is persisted, NO iframe is rendered. Use
        for aggregate questions: COUNT, GROUP BY, breakdowns, time
        series, distinct-value lookups.

        For row-level search building (the answer is a LIST of
        reports the user will browse), use `scout_find_reports`
        instead — that one persists the SQL + renders the viewer
        iframe.

        :param sql: Trino SQL against `delta.default.reports_latest`
            (or `_epic_view`). LIMIT 1000 is enforced server-side
            for safety; if your query is shaped to return fewer
            rows naturally (GROUP BY / COUNT), no LIMIT is needed.
        :return: Markdown-formatted table of rows, suitable for direct
            inclusion in your prose reply.
        """
        bearer = await self._bearer_for_outbound(__oauth_token__)
        await self._emit(__event_emitter__, "Running query…", done=False)
        try:
            agg = await self._post_aggregate(sql, bearer=bearer)
        except ReportViewerServiceError as exc:
            await self._emit(__event_emitter__, f"Failed: {exc}", done=True)
            return f"Error running query: {exc}"
        n = len(agg.get("rows", []))
        await self._emit(__event_emitter__, f"Query complete ({n} rows)", done=True)
        return self._format_aggregate(agg)

    # ------------------------------------------------------------------
    # _import_from_file — CSV/TSV upload → search (private; invoked
    # from scout_find_reports when file_id is set, not exposed as its
    # own tool surface).
    # ------------------------------------------------------------------
    async def _import_from_file(
        self,
        file_id: str,
        id_column: str = "accession_number",
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __oauth_token__: Any = None,
        __metadata__: Optional[dict] = None,
    ) -> Any:
        """Import an external ID list as a Scout search.

        Use when the user uploads a CSV/TSV/TXT of identifiers (patient
        MRNs, accession numbers, message-control-ids) and wants Scout to
        build a search over those reports. The TOOL reads the file
        server-side (you, the LLM, never see its contents — that
        protects PHI from leaking into your context). The backend
        validates each id against reports_latest and returns the count
        of matched + unmatched.

        :param file_id: OWUI file ID of the upload. Get this from the
            chat's file attachments — usually `__files__[0].id` in the
            tool's environment.
        :param id_column: Which Scout column the IDs map to. One of
            'accession_number' (default, most common), 'epic_mrn',
            'message_control_id'. Pick based on the column header or
            the user's prompt.
        :return: Markdown summary with the matched/unmatched counts and
            an iframe of the resulting search. Same renderable format
            as scout_find_reports.
        """
        import csv, io, re

        bearer = await self._bearer_for_outbound(__oauth_token__)

        # Read the file out of OWUI's local storage. Same access path
        # the awl-search-building-chat-poc tool used.
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
        import inspect as _inspect

        file_model = Files.get_file_by_id(file_id)
        if _inspect.iscoroutine(file_model):
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
            if _inspect.iscoroutine(got):
                got = await got
            import os as _os

            if isinstance(got, str) and _os.path.exists(got):
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
                # No header — every row is just an id in column 0.
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

        chat_id = ""
        if isinstance(__metadata__, dict):
            chat_id = str(__metadata__.get("chat_id") or "")

        await self._emit(
            __event_emitter__,
            f"Validating {len(ids)} IDs from {file_model.filename}…",
            done=False,
        )
        url = f"{self.valves.report_viewer_internal_url.rstrip('/')}/api/searches/from-file"
        headers = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        payload = {
            "ids": ids,
            "id_column": id_column,
            "sql_explanation": (
                f"Imported {len(ids)} {id_column} values from "
                f"{file_model.filename}."
            ),
            "owui_chat_id": chat_id,
        }
        async with httpx.AsyncClient(timeout=self.valves.request_timeout_seconds) as c:
            try:
                resp = await c.post(url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                await self._emit(__event_emitter__, f"Import failed: {exc}", done=True)
                return f"Error: {exc}"
        if resp.status_code != 201:
            await self._emit(
                __event_emitter__, f"Import failed: HTTP {resp.status_code}", done=True
            )
            return f"Error: HTTP {resp.status_code} from report-viewer: {resp.text}"
        created = resp.json()
        view_url = created["view_url"]
        await self._emit(
            __event_emitter__,
            f"Matched {created['count']:,} reports from your ID list",
            done=True,
        )
        await self._emit_embed(__event_emitter__, view_url)
        return created.get("summary") or ""

    # ------------------------------------------------------------------
    # scout_get_reports — direct fetch by id, no prior search needed
    # ------------------------------------------------------------------
    async def scout_get_reports(
        self,
        ids: list[str],
        id_column: str = "primary_report_identifier",
        __user__: Optional[dict] = None,
        __oauth_token__: Any = None,
    ) -> Any:
        """
        Fetch full report content (text, sections, diagnoses, metadata)
        by identifier. Use whenever you have an id and want the actual
        report — no prior search needed.

        :param ids: List of identifiers (max 100).
        :param id_column: Which column the ids match. Report-scoped (1
            row each, fast): `primary_report_identifier` (lake file
            path; default), `accession_number`, `message_control_id`.
            Patient-scoped (all reports for that patient):
            `epic_mrn`, `mpi`, `scout_patient_id`. The mrn/mpi columns
            auto-use cross-report patient resolution.
        """
        if not ids:
            return "Error: ids must be a non-empty list."
        if len(ids) > 100:
            return "Error: at most 100 ids per call."
        bearer = await self._bearer_for_outbound(__oauth_token__)
        try:
            result = await self._post_read(ids=ids, id_column=id_column, bearer=bearer)
        except ReportViewerServiceError as exc:
            return f"Error reading reports: {exc}"
        return json.dumps(result, default=str, indent=2)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
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

    @staticmethod
    def _jwt_exp(access_token: str) -> Optional[int]:
        """Extract the exp epoch from a JWT (no signature check)."""
        try:
            payload_b64 = access_token.split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            exp = payload.get("exp")
            return int(exp) if exp is not None else None
        except Exception:
            return None

    async def _bearer_for_outbound(self, oauth: Any) -> Optional[str]:
        """Return the cached OWUI access_token. Warns if past exp."""
        access = self._token_from_owui(oauth)
        if not access:
            return None
        exp = self._jwt_exp(access)
        if exp is not None and exp <= int(time.time()):
            log.warning(
                "forwarding expired OWUI bearer token to report-viewer, may get 401",
                extra={"expired_s_ago": int(time.time()) - exp},
            )
        return access

    async def _post_read(
        self,
        *,
        ids: list[str],
        id_column: str,
        bearer: Optional[str],
    ) -> dict:
        """POST /api/reports/read — direct fetch by id (the
        scout_get_reports backend). Returns `{columns, rows}` with the
        full report content."""
        url = f"{self.valves.report_viewer_internal_url.rstrip('/')}/api/reports/read"
        headers = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        payload: dict[str, Any] = {"ids": ids, "id_column": id_column}
        async with httpx.AsyncClient(timeout=self.valves.request_timeout_seconds) as c:
            r = await c.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise ReportViewerServiceError(_short_error(r))
        return r.json()

    async def _post_aggregate(
        self,
        sql: str,
        *,
        bearer: Optional[str],
    ) -> dict:
        url = f"{self.valves.report_viewer_internal_url.rstrip('/')}/api/reports/query"
        headers = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        payload: dict[str, Any] = {"sql": sql}
        async with httpx.AsyncClient(timeout=self.valves.request_timeout_seconds) as c:
            r = await c.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise ReportViewerServiceError(_short_error(r))
        return r.json()

    @staticmethod
    def _format_aggregate(agg: dict) -> str:
        """Render an aggregate result as a small markdown table so the
        LLM can drop it straight into its prose reply. Service returns
        `{columns, rows, truncated}`. Empty result → a literal "no rows"
        marker the LLM can phrase around."""
        cols: list[str] = agg.get("columns") or []
        rows: list[dict] = agg.get("rows") or []
        if not rows:
            return "Aggregate query returned no rows."

        # Markdown table. Cells get the string form of whatever Trino
        # serialized — pipe characters get escaped so they don't break
        # the row.
        def _cell(v: Any) -> str:
            s = "" if v is None else str(v)
            return s.replace("|", "\\|")

        head = "| " + " | ".join(cols) + " |"
        sep = "| " + " | ".join("---" for _ in cols) + " |"
        body = "\n".join(
            "| " + " | ".join(_cell(r.get(c)) for c in cols) + " |" for r in rows
        )
        note = ""
        if agg.get("truncated"):
            note = (
                f"\n\n_(Result was truncated. Showing the first {len(rows)} rows; "
                f"narrow the query if you need more.)_"
            )
        return head + "\n" + sep + "\n" + body + note

    async def _post_search(
        self,
        sql: str,
        *,
        bearer: Optional[str],
        highlight_terms: Optional[list[str]] = None,
        highlight_diagnosis: Optional[list[str]] = None,
        sql_explanation: Optional[str] = None,
        llm_context_rows: Optional[int] = None,
        owui_chat_id: Optional[str] = None,
    ) -> dict:
        url = f"{self.valves.report_viewer_internal_url.rstrip('/')}/api/searches"
        headers = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        payload: dict[str, Any] = {"sql": sql}
        if highlight_terms:
            payload["highlight_terms"] = highlight_terms
        if highlight_diagnosis:
            payload["highlight_diagnosis"] = highlight_diagnosis
        if sql_explanation:
            payload["sql_explanation"] = sql_explanation
        if llm_context_rows is not None:
            payload["llm_context_rows"] = llm_context_rows
        if owui_chat_id:
            payload["owui_chat_id"] = owui_chat_id
        async with httpx.AsyncClient(timeout=self.valves.request_timeout_seconds) as c:
            r = await c.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise ReportViewerServiceError(_short_error(r))
        return r.json()

    async def _get_rows(
        self, search_id: str, *, page: int, limit: int, bearer: Optional[str]
    ) -> dict:
        url = (
            f"{self.valves.report_viewer_internal_url.rstrip('/')}/api/searches/{search_id}"
            f"/rows?page={page}&limit={limit}"
        )
        headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
        async with httpx.AsyncClient(timeout=self.valves.request_timeout_seconds) as c:
            r = await c.get(url, headers=headers)
        if r.status_code >= 400:
            raise ReportViewerServiceError(_short_error(r))
        return r.json()

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
