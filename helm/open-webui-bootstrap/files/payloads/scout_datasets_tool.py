"""
title: Scout Datasets Tool
description: Save cohort SQL with the Scout datasets-service and surface
             the cohort in chat as a small text summary + an iframe
             linking to the viewer. Rows are evaluated on demand by the
             service (no row materialization); chat carries only the
             dataset_id + view URL.
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
    """LLM-callable functions backed by the datasets-service. All
    methods are namespaced `scout_*` to disambiguate from OWUI
    built-ins (search_notes, view_note, search_chats, etc.).

      * `scout_search_reports(sql, highlight_terms?, sql_explanation?)`
        — POST /datasets, saves the cohort SQL, returns a short summary
        for the LLM plus a viewer iframe for the user. For row-level
        cohort building. When refining, write a NEW call with the
        original conditions plus the new constraint; the SQL is
        standalone, no placeholder substitution.

      * `scout_query_sql(sql)` — POST /datasets/aggregate, runs SQL
        and returns rows directly to LLM context. No persistence, no
        iframe. For aggregate questions (COUNT, GROUP BY, time-series).

      * `scout_read_reports(cohort_id, limit)` — fetch the first N
        rows of an existing cohort with full report text. For "show
        me the impressions of these cases" follow-ups.

      * `scout_import_cohort(file_id, id_column)` — turn a
        researcher-uploaded CSV/TSV of identifiers into a cohort.
        The TOOL reads the file server-side; the LLM never sees the
        contents (PHI doesn't leak into chat context).

    Every method that operates on an existing cohort uses the same
    parameter name `cohort_id` (e.g. `ds_abc123`). No `dataset_id`
    aliases in the public surface.
    """

    class Valves(BaseModel):
        datasets_service_url: str = Field(
            default="http://datasets-service.scout-analytics:8000",
            description=(
                "In-cluster base URL of the datasets-service. The tool "
                "POSTs SQL here and embeds the public `view_url` it "
                "returns into the chat message."
            ),
        )
        public_base_url: str = Field(
            default="",
            description=(
                "Override the iframe's src host. Defaults to whatever the "
                "service returns in `view_url` (which is request-host-"
                "derived). Set this to `https://datasets.<env>.tag.rcif.io` "
                "if the tool ever calls the service via an internal URL "
                "but the iframe needs to load via the public ingress."
            ),
        )
        iframe_height_px: int = Field(default=500, ge=200, le=1200)
        request_timeout_seconds: int = Field(default=120, ge=10, le=600)
        # Keycloak refresh credentials — used to proactively refresh
        # OWUI's cached access token if it's near expiry by the time we
        # dispatch a tool call. OWUI's own refresh loop runs on a schedule
        # that can drift across the access-token lifespan, leaving us
        # forwarding a stale bearer to datasets-service (which 401s).
        # When all three are set, the tool calls Keycloak's token endpoint
        # with `__oauth_token__.refresh_token` to mint a fresh access token
        # right before its outbound POST. When any are empty, the tool
        # falls back to the cached bearer as-is.
        keycloak_token_url: str = Field(default="")
        keycloak_client_id: str = Field(default="")
        keycloak_client_secret: str = Field(default="")
        # Refresh when the cached access_token's `exp` claim is within
        # this many seconds of `now`. 60s gives plenty of headroom for
        # the round trip + clock skew.
        token_refresh_threshold_seconds: int = Field(default=60, ge=5, le=300)

    def __init__(self) -> None:
        self.valves = self.Valves()

    # ------------------------------------------------------------------
    # scout_search_reports — materialize a SQL query into a cohort
    # ------------------------------------------------------------------
    async def scout_search_reports(
        self,
        sql: str,
        highlight_terms: Optional[list[str]] = None,
        sql_explanation: Optional[str] = None,
        llm_context_rows: Optional[int] = None,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __oauth_token__: Any = None,
        __metadata__: Optional[dict] = None,
    ) -> Any:
        """
        Build a saved cohort from a SQL query. Saves the cohort SQL,
        renders the cohort viewer iframe in chat, and returns a rich
        summary (count + columns + sample table + anti-restatement
        directive) to your context. Full report text never enters your
        context — the user sees all rows in the iframe and can click
        any row to expand into the full report (with highlight terms
        emphasized) on demand.

        Use for cohort building (row-level discovery). For aggregate
        questions (COUNT, GROUP BY, time-series — anything where the
        answer is a number or a small summary table) use
        `scout_query_sql` instead — it doesn't persist anything and
        returns rows directly to your context.

        Refinement: when the user asks to narrow the cohort ("only MRs",
        "drop the under-18 patients"), write a NEW `scout_search_reports`
        call with the original conditions plus the new constraint.
        Each cohort is a standalone saved query — there is no
        placeholder substitution or parent-cohort SQL injection. The
        SPA homepage groups the chain by chat for the user's benefit.

        Your SELECT must include one of `message_control_id`,
        `accession_number`, or `epic_mrn`. The canonical row-level
        SELECT is `message_control_id, accession_number, modality,
        service_name, message_dt, patient_age, sex`. The SPA fetches
        full report text + diagnoses on row-expand (no need to include
        them in your SELECT). Prefer specific projections over `SELECT *`.

        :param sql: Trino SQL against `delta.default.reports_latest`
            (or `_epic_view`). Each cohort SQL stands alone — no
            placeholder substitution.
        :param highlight_terms: Clinical terms for the row-expand
            viewer to highlight in the report text. Good values:
            `["pulmonary embolism", "PE"]`, `["cerebral infarction"]`,
            `["glioblastoma", "GBM"]`. BAD values: `["brain"]`,
            `["chest"]`, `["MRI"]` — those belong in the SQL itself,
            not as highlight markers. UI-only: highlighting does NOT
            filter rows. The SQL is what decides what's in the cohort;
            these strings just make the matches visually obvious when
            the user expands a row. Without highlight_terms, you get
            no `snippet` field on sample rows — pass them so you can
            see why each row matched.
        :param sql_explanation: One- to three-sentence plain-language
            description of what the SQL matches and why. Surfaced in
            the SPA's "About this cohort" panel so the user can
            sanity-check what was built without reading raw SQL.
            Example: "Patients with chest CT reports from 2024+ that
            mention pulmonary nodules in the impression or findings
            section, excluding any negated mentions like 'no nodule'.
            ICD-coded R91% diagnoses are also included regardless of
            text negation." Required on every cohort.
        :param llm_context_rows: Sample-row cap. Default 5.
        :return: Rich text summary + an embedded `<iframe>` of the
            viewer. Service renders the summary; tool just forwards it.
        """
        bearer = await self._bearer_for_outbound(__oauth_token__)

        await self._emit(__event_emitter__, "Searching reports…", done=False)
        try:
            # OWUI injects chat metadata into the tool call. Pull the
            # chat ID + title so the SPA homepage can group cohorts by
            # conversation. Best-effort: probe several possible shapes
            # since OWUI metadata structure varies across versions.
            chat_id = ""
            chat_title = ""
            if isinstance(__metadata__, dict):
                chat_id = str(__metadata__.get("chat_id") or "")
                # Title can live in several places depending on OWUI version.
                title_candidates = [
                    __metadata__.get("chat_title"),
                    __metadata__.get("title"),
                    (
                        (__metadata__.get("chat") or {}).get("title")
                        if isinstance(__metadata__.get("chat"), dict)
                        else None
                    ),
                    (
                        (__metadata__.get("session") or {}).get("title")
                        if isinstance(__metadata__.get("session"), dict)
                        else None
                    ),
                ]
                for c in title_candidates:
                    if c:
                        chat_title = str(c)
                        break
                # One-time INFO log so we can see what OWUI actually
                # gives us in the cluster logs and adjust this list.
                log.info(
                    "owui tool metadata",
                    extra={
                        "metadata_keys": list(__metadata__.keys()),
                        "chat_id": chat_id,
                        "chat_title": chat_title,
                    },
                )

            created = await self._post_dataset(
                sql,
                bearer=bearer,
                highlight_terms=highlight_terms,
                sql_explanation=sql_explanation,
                llm_context_rows=llm_context_rows,
                owui_chat_id=chat_id,
                owui_chat_title=chat_title,
            )
        except DatasetsServiceError as exc:
            await self._emit(__event_emitter__, f"Failed: {exc}", done=True)
            return f"Error fetching reports: {exc}"

        dataset_id = created["dataset_id"]
        count = created["count"]
        view_url = self._resolve_view_url(created["view_url"])

        await self._emit(
            __event_emitter__, f"Found {count:,} matching reports", done=True
        )

        # Push the viewer URL into OWUI's `message.embeds` field —
        # scout_search_reports always saves the cohort + renders the
        # iframe. Aggregate / GROUP-BY questions use `scout_query_sql`
        # instead (ephemeral, no iframe).
        #
        # `replace: true` keeps a single iframe per message even if
        # the LLM iterates scout_search_reports mid-turn. The embeds
        # field is separate from message.content so this works
        # around OWUI 0.9.5's outlet-filter + message-event injection
        # gaps.
        await self._emit_embed(__event_emitter__, view_url)

        # The service returns the rich LLM-bound markdown summary
        # (count + columns + USER DISPLAY directive + sample table
        # + internal cohort handle). Return it verbatim — the LLM uses
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
        context. NO cohort is persisted, NO iframe is rendered. Use
        for aggregate questions: COUNT, GROUP BY, breakdowns, time
        series, distinct-value lookups.

        For row-level cohort building (the answer is a LIST of
        reports the user will browse), use `scout_search_reports`
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
        except DatasetsServiceError as exc:
            await self._emit(__event_emitter__, f"Failed: {exc}", done=True)
            return f"Error running query: {exc}"
        n = len(agg.get("rows", []))
        await self._emit(__event_emitter__, f"Query complete ({n} rows)", done=True)
        return self._format_aggregate(agg)

    # ------------------------------------------------------------------
    # scout_import_cohort — CSV/TSV upload → cohort
    # ------------------------------------------------------------------
    async def scout_import_cohort(
        self,
        file_id: str,
        id_column: str = "accession_number",
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __oauth_token__: Any = None,
        __metadata__: Optional[dict] = None,
    ) -> Any:
        """Import an external ID list into a Scout cohort.

        Use when the user uploads a CSV/TSV/TXT of identifiers (patient
        MRNs, accession numbers, message-control-ids) and wants Scout to
        build a cohort over those reports. The TOOL reads the file
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
            an iframe of the resulting cohort. Same renderable format
            as scout_search_reports.
        """
        import csv, io, re

        bearer = await self._bearer_for_outbound(__oauth_token__)

        # Read the file out of OWUI's local storage. Same access path
        # the awl-cohort-building-chat-poc tool used.
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
        import sys

        file_model = Files.get_file_by_id(file_id)
        if _inspect.iscoroutine(file_model):
            file_model = await file_model
        if not file_model:
            return f"Error: file {file_id} not found in OWUI"
        print(
            f"[scout_import_cohort] file_id={file_id!r} "
            f"filename={getattr(file_model, 'filename', None)!r} "
            f"path={getattr(file_model, 'path', None)!r}",
            file=sys.stderr,
            flush=True,
        )
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
            print(
                f"[scout_import_cohort] Storage.get_file raised: {exc!r}",
                file=sys.stderr,
                flush=True,
            )
            return f"Error: could not read file {file_id}: {exc}"
        print(
            f"[scout_import_cohort] contents type={type(contents).__name__} "
            f"len={len(contents) if hasattr(contents, '__len__') else 'n/a'}",
            file=sys.stderr,
            flush=True,
        )
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
        chat_title = ""
        if isinstance(__metadata__, dict):
            chat_id = str(__metadata__.get("chat_id") or "")
            for c in [
                __metadata__.get("chat_title"),
                __metadata__.get("title"),
                (
                    (__metadata__.get("chat") or {}).get("title")
                    if isinstance(__metadata__.get("chat"), dict)
                    else None
                ),
            ]:
                if c:
                    chat_title = str(c)
                    break

        await self._emit(
            __event_emitter__,
            f"Validating {len(ids)} IDs from {file_model.filename}…",
            done=False,
        )
        url = f"{self.valves.datasets_service_url.rstrip('/')}/datasets/from-ids"
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
            "owui_chat_title": chat_title,
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
            return f"Error: HTTP {resp.status_code} from datasets-service: {resp.text}"
        created = resp.json()
        view_url = self._resolve_view_url(created["view_url"])
        await self._emit(
            __event_emitter__,
            f"Matched {created['count']:,} reports from your ID list",
            done=True,
        )
        await self._emit_embed(__event_emitter__, view_url)
        return created.get("summary") or ""

    # ------------------------------------------------------------------
    # scout_read_reports — fetch full report rows from a cohort
    # ------------------------------------------------------------------
    async def scout_read_reports(
        self,
        cohort_id: str,
        limit: int = 10,
        __user__: Optional[dict] = None,
        __oauth_token__: Any = None,
    ) -> Any:
        """
        Fetch the first `limit` rows of an existing cohort, including
        their full report text. Use this after `scout_search_reports`
        when the user asks for specifics ("show me the impressions",
        "what did report X say").

        :param cohort_id: A cohort handle from a prior
            `scout_search_reports` or `scout_import_cohort` call
            (e.g. `ds_aB3zX9`).
        :param limit: max rows to return to your context (1-100;
            default 10).
        """
        if limit < 1 or limit > 100:
            return "Error: limit must be between 1 and 100."
        bearer = await self._bearer_for_outbound(__oauth_token__)
        try:
            rows = await self._get_rows(cohort_id, page=1, limit=limit, bearer=bearer)
        except DatasetsServiceError as exc:
            return f"Error reading cohort: {exc}"
        return json.dumps(rows, default=str, indent=2)

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
    def _refresh_token_from_owui(oauth: Any) -> Optional[str]:
        if isinstance(oauth, dict):
            return oauth.get("refresh_token") or None
        return None

    @staticmethod
    def _jwt_exp(access_token: str) -> Optional[int]:
        """Extract the `exp` epoch from a JWT without verifying the
        signature. We're not authenticating against this token — we just
        want to know whether to refresh before forwarding."""
        try:
            payload_b64 = access_token.split(".")[1]
            # JWT base64url, may lack padding.
            payload_b64 += "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            exp = payload.get("exp")
            return int(exp) if exp is not None else None
        except Exception:
            return None

    async def _bearer_for_outbound(self, oauth: Any) -> Optional[str]:
        """Return an access_token to use on the outbound POST. If the
        cached token is within `token_refresh_threshold_seconds` of
        expiry AND we have refresh-token + Keycloak client creds
        configured, mint a fresh one via the refresh_token grant before
        returning. Otherwise return the cached token as-is so the caller
        can still try (and degrade to today's 401 behavior if stale)."""
        access = self._token_from_owui(oauth)
        if not access:
            return None
        exp = self._jwt_exp(access)
        if exp is None:
            return access
        remaining = exp - int(time.time())
        if remaining > self.valves.token_refresh_threshold_seconds:
            return access
        refresh = self._refresh_token_from_owui(oauth)
        if not (
            refresh
            and self.valves.keycloak_token_url
            and self.valves.keycloak_client_id
            and self.valves.keycloak_client_secret
        ):
            log.info(
                "bearer near-expiry but cannot refresh "
                "(missing refresh_token or client creds); forwarding as-is",
                extra={"remaining_s": remaining},
            )
            return access
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": self.valves.keycloak_client_id,
            "client_secret": self.valves.keycloak_client_secret,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(self.valves.keycloak_token_url, data=data)
            if r.status_code != 200:
                log.warning(
                    "keycloak refresh failed; forwarding stale bearer",
                    extra={"status": r.status_code, "body": r.text[:200]},
                )
                return access
            new_access = r.json().get("access_token")
            if not new_access:
                return access
            log.info(
                "refreshed OWUI bearer via keycloak",
                extra={"prev_remaining_s": remaining},
            )
            return new_access
        except Exception:
            log.exception("keycloak refresh raised; forwarding stale bearer")
            return access

    def _resolve_view_url(self, service_view_url: str) -> str:
        """If `public_base_url` is set, swap the host so the iframe
        loads via the public ingress regardless of which URL the
        service computed from the request host."""
        if not self.valves.public_base_url:
            return service_view_url
        # service_view_url looks like 'http://datasets-service/datasets/ds_xxx/view'
        # Replace the scheme+host with the public base.
        try:
            path = service_view_url.split("/", 3)[-1]
        except Exception:
            return service_view_url
        return f"{self.valves.public_base_url.rstrip('/')}/{path}"

    async def _post_aggregate(
        self,
        sql: str,
        *,
        bearer: Optional[str],
    ) -> dict:
        url = f"{self.valves.datasets_service_url.rstrip('/')}/datasets/aggregate"
        headers = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        payload: dict[str, Any] = {"sql": sql}
        async with httpx.AsyncClient(timeout=self.valves.request_timeout_seconds) as c:
            r = await c.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise DatasetsServiceError(_short_error(r))
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

    async def _post_dataset(
        self,
        sql: str,
        *,
        bearer: Optional[str],
        highlight_terms: Optional[list[str]] = None,
        sql_explanation: Optional[str] = None,
        llm_context_rows: Optional[int] = None,
        owui_chat_id: Optional[str] = None,
        owui_chat_title: Optional[str] = None,
    ) -> dict:
        url = f"{self.valves.datasets_service_url.rstrip('/')}/datasets"
        headers = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        payload: dict[str, Any] = {"sql": sql}
        if highlight_terms:
            payload["highlight_terms"] = highlight_terms
        if sql_explanation:
            payload["sql_explanation"] = sql_explanation
        if llm_context_rows is not None:
            payload["llm_context_rows"] = llm_context_rows
        if owui_chat_id:
            payload["owui_chat_id"] = owui_chat_id
        if owui_chat_title:
            payload["owui_chat_title"] = owui_chat_title
        async with httpx.AsyncClient(timeout=self.valves.request_timeout_seconds) as c:
            r = await c.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise DatasetsServiceError(_short_error(r))
        return r.json()

    async def _get_rows(
        self, dataset_id: str, *, page: int, limit: int, bearer: Optional[str]
    ) -> dict:
        url = (
            f"{self.valves.datasets_service_url.rstrip('/')}/datasets/{dataset_id}"
            f"/rows?page={page}&limit={limit}"
        )
        headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
        async with httpx.AsyncClient(timeout=self.valves.request_timeout_seconds) as c:
            r = await c.get(url, headers=headers)
        if r.status_code >= 400:
            raise DatasetsServiceError(_short_error(r))
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
        scout_search_reports calls within one turn don't stack iframes."""
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


class DatasetsServiceError(RuntimeError):
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
