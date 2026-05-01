"""
title: Scout Query Tool
description: Execute SQL queries against Scout Delta Lake. Applies negation
             filtering to free-text searches, saves per-query JSON to OWUI
             Files (chat-scoped, last 10 retained), and renders a display-aware
             iframe with a Download CSV button. Submission to XNAT happens
             via the Send-to-XNAT Action on the message toolbar — that Action
             reads the saved JSON, runs a review step, and posts.
author: Scout Team
version: 0.2.0
"""

from __future__ import annotations

import asyncio
import csv
import html as html_module
import io
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


# ============================================================================
# Negation filtering — ported from analytics/notebooks/cohort/cohort_builder.py
# (lines 327-420). Keep these patterns in sync with the playbook.
# ============================================================================

DEFAULT_NEGATION_PATTERNS = [
    r"no\s+(mri?\s+)?evidence",
    r"without\s+(mri?\s+)?evidence",
    r"negative\s+for",
    r"absence\s+of",
    r"ruled?\s+out",
    r"rules?\s+out",
    r"excluding",
    r"excluded",
    r"evaluat(?:e|ion)\s+(for)?",
    r"concern\s+(for)?",
    r"no\s+\w+\s+(suggest|indication|sign)",
    r"no\s+",
]


def _default_negation_regex() -> re.Pattern:
    return re.compile("|".join(DEFAULT_NEGATION_PATTERNS), re.IGNORECASE)


def _check_negation_before_match(
    report_lower: str,
    match_start: int,
    negation_regex: re.Pattern,
    context_chars: int = 50,
) -> bool:
    """Return True if a negation phrase appears in the 50 chars before the match."""
    start_pos = max(0, match_start - context_chars)
    context_before = report_lower[start_pos:match_start]
    return bool(negation_regex.search(context_before))


def _has_positive_mention(
    report_text: Optional[str],
    search_patterns: list[str],
    negation_regex: Optional[re.Pattern] = None,
) -> bool:
    """True if report has at least one match without preceding negation."""
    if not report_text:
        return False
    if negation_regex is None:
        negation_regex = _default_negation_regex()

    report_lower = report_text.lower()
    matches: list[re.Match] = []
    for pattern in search_patterns:
        try:
            matches.extend(
                re.finditer(pattern, report_lower, re.IGNORECASE | re.DOTALL)
            )
        except re.error:
            continue

    if not matches:
        return False

    for match in matches:
        if not _check_negation_before_match(
            report_lower, match.start(), negation_regex
        ):
            return True
    return False


# ============================================================================
# Display constants
# ============================================================================

VALID_DISPLAYS = {"none", "count", "breakdown", "table", "detail"}

# Defaults for how many rows go to the LLM context per display mode.
# None means "all rows" (capped by safety_max_context_rows in Valves).
DISPLAY_LLM_ROW_DEFAULTS = {
    "none": None,
    "count": 1,
    "breakdown": None,
    "table": 5,
    "detail": None,
}


class Tools:
    """Query Scout Delta Lake; render display-aware UI; persist per-query JSON."""

    class Valves(BaseModel):
        trino_host: str = Field(
            default="trino",
            description="Trino hostname",
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
        safety_max_context_rows: int = Field(
            default=200,
            description="Hard cap on rows sent to LLM context regardless of llm_context_rows",
        )
        owui_files_lru_keep: int = Field(
            default=3,
            description="Last N per-query JSON files to retain per chat in OWUI Files",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    # ------------------------------------------------------------------
    # LLM-callable entry point
    # ------------------------------------------------------------------
    async def query(
        self,
        sql: str,
        display: str = "table",
        llm_context_rows: Optional[int] = None,
        negation_search_patterns: Optional[list[str]] = None,
        bypass_negation_column: str = "",
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __chat_id__: str = "",
    ) -> Any:
        """
        Execute a SQL query against the Scout Delta Lake reports table.

        :param sql: Trino SQL against `delta.default.reports`. Always filter
            on the `year` partition.
        :param display: How to render results to the user. One of: "none"
            (no iframe; answer in prose), "count" (stat card), "breakdown"
            (bar chart), "table" (paginated table), "detail" (report cards
            with findings/impression text). Default: "table".
        :param llm_context_rows: Override how many rows are returned to your
            context. None uses display-driven defaults (5 for table, 1 for
            count, all for breakdown/detail/none). Capped by
            `safety_max_context_rows` Valve.
        :param negation_search_patterns: Regex patterns matching the positive
            search terms in your SQL. REQUIRED whenever your SQL searches
            free-text columns. Without it, "no evidence of X" leaks through.
        :param bypass_negation_column: Optional name of a boolean column in
            your SELECT. Rows where it's true are considered cohort matches
            regardless of text negation (the ICD-coded bypass path).
        :return: Summary text + sample rows for your context. The user sees
            an inline iframe rendered according to `display`.
        """
        if __user__ is None:
            __user__ = {}
        if negation_search_patterns is None:
            negation_search_patterns = []

        if display not in VALID_DISPLAYS:
            display = "table"

        await self._emit_status(__event_emitter__, "Executing query…", done=False)

        try:
            rows, columns = await self._execute_trino_query(sql)
        except Exception as exc:
            log.exception("Trino query failed")
            await self._emit_status(
                __event_emitter__, f"Query failed: {exc}", done=True
            )
            return f"Error executing query: {exc}"

        if not rows:
            await self._emit_status(
                __event_emitter__, "Query complete — no results", done=True
            )
            return "The query returned no results."

        # Apply negation filtering — annotates each row with three flags.
        negation_applied, included_count, excluded_count = self._apply_negation(
            rows, columns, negation_search_patterns, bypass_negation_column
        )

        # Tier 1: write full JSON to OWUI Files (always; hot path).
        user_id = __user__.get("id", "") if isinstance(__user__, dict) else ""
        file_id = ""
        try:
            file_id = await self._save_to_owui_files(
                rows=rows,
                columns=columns,
                sql=sql,
                negation_search_patterns=negation_search_patterns,
                bypass_negation_column=bypass_negation_column,
                negation_applied=negation_applied,
                user_id=user_id,
                chat_id=__chat_id__,
            )
        except Exception:
            log.exception("Failed to save JSON to OWUI Files")

        # Async LRU prune of OWUI Files (fire-and-forget; doesn't block response).
        if user_id and __chat_id__ and file_id:
            asyncio.create_task(
                self._prune_owui_files(
                    user_id, __chat_id__, self.valves.owui_files_lru_keep
                )
            )

        await self._emit_status(__event_emitter__, "Query complete", done=True)

        # Build LLM context summary.
        sample_rows = self._select_sample_rows(rows, display, llm_context_rows)
        summary = self._build_summary(
            columns=columns,
            total_rows=len(rows),
            sample_rows=sample_rows,
            negation_applied=negation_applied,
            included_count=included_count,
            excluded_count=excluded_count,
            negation_search_patterns=negation_search_patterns,
            bypass_negation_column=bypass_negation_column,
        )

        # Build user-facing render based on display intent.
        rich_ui = self._build_rich_ui(columns, rows, display)

        if rich_ui is None:
            # display=none → no iframe; LLM answers in prose.
            return summary

        return (
            HTMLResponse(content=rich_ui, headers={"Content-Disposition": "inline"}),
            summary,
        )

    # ------------------------------------------------------------------
    # Trino — using REST API directly via httpx (no external `trino` package)
    # ------------------------------------------------------------------
    async def _execute_trino_query(self, sql: str) -> tuple[list[dict], list[str]]:
        """
        Execute a Trino query via the /v1/statement HTTP endpoint.

        Trino's REST API returns results in chunks: the first POST returns a
        statement reference + (optionally) the first chunk; subsequent GETs to
        `nextUri` paginate through remaining chunks until `nextUri` is absent.
        """
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
                # Capture column metadata from the first response that has it
                if not columns and "columns" in data:
                    columns = [c["name"] for c in data["columns"]]

                # Accumulate any data rows in this chunk
                chunk = data.get("data") or []
                for raw_row in chunk:
                    rows.append({columns[i]: raw_row[i] for i in range(len(columns))})

                # Check for terminal failure state
                state = (data.get("stats") or {}).get("state")
                error = data.get("error")
                if error:
                    msg = error.get("message", "Trino error")
                    raise RuntimeError(f"{msg} (state={state})")

                next_uri = data.get("nextUri")
                if not next_uri:
                    break

                resp = await client.get(next_uri, headers=headers)
                resp.raise_for_status()
                data = resp.json()

        return rows, columns

    # ------------------------------------------------------------------
    # Negation
    # ------------------------------------------------------------------
    def _apply_negation(
        self,
        rows: list[dict],
        columns: list[str],
        search_patterns: list[str],
        bypass_column: str,
    ) -> tuple[bool, int, int]:
        """
        Annotate each row in-place with three flags:
          - has_strong_indicator: bypass_column is truthy for the row
          - has_positive_mention: strong indicator OR (text match + no preceding negation)
          - included_in_cohort: same as has_positive_mention (cohort-builder convention)

        Returns (was_applied, included_count, excluded_count).
        """
        if not search_patterns:
            # No negation requested — flag all rows as included.
            for row in rows:
                row["has_strong_indicator"] = False
                row["has_positive_mention"] = True
                row["included_in_cohort"] = True
            return False, len(rows), 0

        negation_regex = _default_negation_regex()
        included = 0
        for row in rows:
            strong = False
            if bypass_column and bypass_column in row:
                strong = bool(row.get(bypass_column))

            if strong:
                row["has_strong_indicator"] = True
                row["has_positive_mention"] = True
                row["included_in_cohort"] = True
                included += 1
                continue

            text = row.get("report_text")
            positive = _has_positive_mention(text, search_patterns, negation_regex)
            row["has_strong_indicator"] = False
            row["has_positive_mention"] = positive
            row["included_in_cohort"] = positive
            if positive:
                included += 1

        return True, included, len(rows) - included

    # ------------------------------------------------------------------
    # OWUI Files persistence
    # ------------------------------------------------------------------
    async def _save_to_owui_files(
        self,
        rows: list[dict],
        columns: list[str],
        sql: str,
        negation_search_patterns: list[str],
        bypass_negation_column: str,
        negation_applied: bool,
        user_id: str,
        chat_id: str,
    ) -> str:
        """Write the per-query JSON to OWUI Files with meta.chat_id scoping."""
        from open_webui.models.files import Files, FileForm
        from open_webui.storage.provider import Storage

        if not user_id:
            log.warning("No user_id; skipping OWUI Files write")
            return ""

        file_id = str(uuid.uuid4())
        filename = f"scout_query_result_{file_id}.json"

        # Augment columns to include the flag columns we annotated.
        out_columns = list(columns)
        for c in ("has_strong_indicator", "has_positive_mention", "included_in_cohort"):
            if c not in out_columns:
                out_columns.append(c)

        payload = {
            "schema_version": 1,
            "file_id": file_id,
            "chat_id": chat_id,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "sql": sql,
            "negation_search_patterns": list(negation_search_patterns),
            "bypass_negation_column": bypass_negation_column,
            "negation_filter_applied": negation_applied,
            "columns": out_columns,
            "rows": rows,
        }

        body = json.dumps(payload, default=str).encode("utf-8")

        # OWUI Storage / Files API may be sync or async depending on version —
        # await coroutines if they come back, otherwise use the value directly.
        import inspect

        upload_result = Storage.upload_file(io.BytesIO(body), filename, {})
        if inspect.isawaitable(upload_result):
            upload_result = await upload_result
        _contents, file_path = upload_result

        insert_result = Files.insert_new_file(
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
                    "scout_query_result": True,
                },
            ),
        )
        if inspect.isawaitable(insert_result):
            await insert_result
        return file_id

    async def _prune_owui_files(self, user_id: str, chat_id: str, keep_n: int) -> None:
        """Async LRU prune of `scout_query_result_*.json` for this chat."""
        try:
            import inspect
            from open_webui.models.files import Files
            from open_webui.storage.provider import Storage

            # Files.get_files_by_user_id may be sync or async depending on
            # the OWUI version — handle both.
            result = Files.get_files_by_user_id(user_id)
            if inspect.isawaitable(result):
                all_files = await result
            else:
                all_files = result

            ours = [
                f
                for f in all_files
                if (f.meta or {}).get("chat_id") == chat_id
                and (f.meta or {}).get("scout_query_result")
            ]
            ours.sort(key=lambda f: f.created_at or 0, reverse=True)

            for old in ours[keep_n:]:
                try:
                    storage_result = Storage.delete_file(old.path)
                    if inspect.isawaitable(storage_result):
                        await storage_result
                except Exception:
                    log.exception("Failed to delete storage for %s", old.id)
                try:
                    files_result = Files.delete_file_by_id(old.id)
                    if inspect.isawaitable(files_result):
                        await files_result
                except Exception:
                    log.exception("Failed to delete Files record %s", old.id)
        except Exception:
            log.exception("LRU prune failed")

    # ------------------------------------------------------------------
    # LLM context: sample selection + summary
    # ------------------------------------------------------------------
    def _select_sample_rows(
        self,
        rows: list[dict],
        display: str,
        llm_context_rows: Optional[int],
    ) -> list[dict]:
        """How many rows the LLM gets in context, gated by safety cap."""
        if llm_context_rows is None:
            llm_context_rows = DISPLAY_LLM_ROW_DEFAULTS.get(display)

        if llm_context_rows is None:
            n = len(rows)
        else:
            n = min(llm_context_rows, len(rows))

        n = min(n, self.valves.safety_max_context_rows)
        return rows[:n]

    def _build_summary(
        self,
        columns: list[str],
        total_rows: int,
        sample_rows: list[dict],
        negation_applied: bool,
        included_count: int,
        excluded_count: int,
        negation_search_patterns: list[str],
        bypass_negation_column: str,
    ) -> str:
        """Compact text summary for LLM context."""
        parts: list[str] = []
        parts.append(
            f"Query returned {total_rows} row{'s' if total_rows != 1 else ''} "
            f"across {len(columns)} columns."
        )
        parts.append(f"Columns: {', '.join(columns)}")

        if negation_applied:
            parts.append(
                f"Negation filter: APPLIED with patterns "
                f"{negation_search_patterns!r}"
                + (
                    f", bypass column '{bypass_negation_column}'"
                    if bypass_negation_column
                    else ""
                )
                + f". Included: {included_count}, Excluded: {excluded_count}."
            )
        else:
            parts.append(
                "Negation filter: NOT applied (no negation_search_patterns provided). "
                "If your SQL searches free-text columns, you should re-run with "
                "negation_search_patterns to filter false positives."
            )

        if sample_rows:
            n_sample = len(sample_rows)
            parts.append("")
            parts.append(f"Sample ({n_sample} of {total_rows} rows):")
            # Format as simple aligned table
            header = " | ".join(columns)
            sep = "-+-".join("-" * len(c) for c in columns)
            body_lines = [
                " | ".join(str(row.get(c, ""))[:120] for c in columns)
                for row in sample_rows
            ]
            parts.append(header)
            parts.append(sep)
            parts.extend(body_lines)
            if total_rows > n_sample:
                parts.append(
                    f"\n... and {total_rows - n_sample} more rows (not in your context — "
                    f"refer to the rendered display, do not enumerate)."
                )

        parts.append("")
        parts.append(
            "Tell the user they can use the 'Send to XNAT' button on this "
            "message to review and submit the cohort."
        )

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # User-facing render
    # ------------------------------------------------------------------
    def _build_rich_ui(
        self,
        columns: list[str],
        rows: list[dict],
        display: str,
    ) -> Optional[str]:
        """Render the iframe HTML based on display mode. Returns None if display=none."""
        if display == "none":
            return None

        body_html: str
        if display == "count":
            body_html = self._render_count(rows)
        elif display == "breakdown":
            body_html = self._render_breakdown(columns, rows)
        elif display == "detail":
            body_html = self._render_detail(columns, rows)
        else:  # "table"
            body_html = self._render_table(columns, rows)

        # Top-of-iframe toolbar with action buttons.
        n_rows = len(rows)

        # CSV download — Flavin's original pattern: button onclick triggers a
        # JS handler that creates a Blob and programmatically clicks an
        # anchor with a `download` attribute. Sandboxed iframes with
        # `allow-downloads` honor this and produce a properly named file.
        csv_bytes = self._rows_to_csv_bytes(columns, rows)
        import base64

        csv_b64 = base64.b64encode(csv_bytes).decode("ascii")

        action_buttons: list[str] = []
        action_buttons.append(
            '<button class="btn" onclick="downloadCsv()">Download CSV</button>'
        )

        toolbar = (
            f'<div class="toolbar">'
            f'<span class="info">{n_rows} row{"s" if n_rows != 1 else ""}, '
            f"{len(columns)} columns</span>"
            f'<div class="actions">{"".join(action_buttons)}</div>'
            f"</div>"
        )

        # Wrap body in a collapsible section so the Hide button can compact it
        return self._wrap_html(
            toolbar + f'<div class="body">{body_html}</div>',
            csv_b64=csv_b64,
        )

    def _render_count(self, rows: list[dict]) -> str:
        """Single-stat card. Uses the first numeric value in the first row."""
        if not rows:
            return '<div class="stat-card"><div class="stat-value">0</div></div>'
        first = rows[0]
        # Pick first integer-ish value as the count
        count: Any = None
        label: str = ""
        for k, v in first.items():
            if isinstance(v, (int, float)):
                count = v
                label = k
                break
        if count is None:
            count = len(rows)
            label = "rows"
        return (
            f'<div class="stat-card">'
            f'<div class="stat-value">{html_module.escape(str(count))}</div>'
            f'<div class="stat-label">{html_module.escape(label)}</div>'
            f"</div>"
        )

    def _render_breakdown(self, columns: list[str], rows: list[dict]) -> str:
        """Bar chart from the first two columns: category, value."""
        if len(columns) < 2 or not rows:
            return self._render_table(columns, rows)

        cat_col = columns[0]
        val_col = columns[1]

        try:
            values = [
                (str(r.get(cat_col, "")), float(r.get(val_col, 0) or 0)) for r in rows
            ]
        except (TypeError, ValueError):
            return self._render_table(columns, rows)

        if not values:
            return self._render_table(columns, rows)

        max_val = max(v for _, v in values) or 1
        bars: list[str] = []
        for cat, val in values:
            pct = (val / max_val) * 100
            bars.append(
                f'<div class="bar-row">'
                f'<div class="bar-label">{html_module.escape(cat)}</div>'
                f'<div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%"></div></div>'
                f'<div class="bar-value">{html_module.escape(str(val))}</div>'
                f"</div>"
            )
        return f'<div class="breakdown">{"".join(bars)}</div>'

    def _render_detail(self, columns: list[str], rows: list[dict]) -> str:
        """Cards-style render emphasizing report sections."""
        if not rows:
            return '<div class="empty">No results.</div>'

        cards: list[str] = []
        for row in rows:
            # Header from common ID columns
            header_bits: list[str] = []
            for c in (
                "accession_number",
                "patient_age",
                "sex",
                "modality",
                "service_name",
                "message_dt",
            ):
                if c in row and row[c] not in (None, ""):
                    header_bits.append(
                        f'<span class="card-meta"><b>{html_module.escape(c)}:</b> '
                        f"{html_module.escape(str(row[c]))}</span>"
                    )

            sections: list[str] = []
            for c in columns:
                if c.startswith("report_section_") or c == "report_text":
                    val = row.get(c)
                    if val:
                        sections.append(
                            f'<div class="card-section">'
                            f'<div class="card-section-title">{html_module.escape(c)}</div>'
                            f'<div class="card-section-body">'
                            f"{html_module.escape(str(val))}"
                            f"</div></div>"
                        )

            inclusion = ""
            if "included_in_cohort" in row:
                included = bool(row.get("included_in_cohort"))
                cls = "tag-included" if included else "tag-excluded"
                lbl = "INCLUDED" if included else "EXCLUDED"
                inclusion = f'<span class="tag {cls}">{lbl}</span>'

            cards.append(
                f'<div class="card">'
                f'<div class="card-header">{inclusion}{"".join(header_bits)}</div>'
                f'{"".join(sections)}'
                f"</div>"
            )
        return f'<div class="cards">{"".join(cards)}</div>'

    def _render_table(self, columns: list[str], rows: list[dict]) -> str:
        """Standard paginated table view."""
        th_cells = "".join(f"<th>{html_module.escape(c)}</th>" for c in columns)
        tr_rows: list[str] = []
        for row in rows:
            td_cells = "".join(
                f"<td>{html_module.escape(str(row.get(c, '')))}</td>" for c in columns
            )
            tr_rows.append(f"<tr>{td_cells}</tr>")
        tbody = "\n".join(tr_rows)
        return (
            '<div class="table-wrap">'
            f"<table><thead><tr>{th_cells}</tr></thead>"
            f"<tbody>{tbody}</tbody></table>"
            "</div>"
        )

    def _wrap_html(self, body: str, csv_b64: str = "") -> str:
        """Wrap body content in HTML with stylesheet + height-reporting JS + CSV blob loader."""
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
        display: flex; align-items: center; justify-content: space-between;
        padding: 8px 12px; background: #2a2a2a; border-bottom: 1px solid #3a3a3a;
        position: sticky; top: 0; z-index: 10;
    }}
    .toolbar .info {{ color: #aaa; }}
    .toolbar .actions {{ display: flex; gap: 6px; }}
    .btn {{
        background: #3a3a3a; color: #e0e0e0; border: 1px solid #555;
        padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 12px;
        text-decoration: none; display: inline-block;
    }}
    .btn:hover {{ background: #4a4a4a; }}
    .btn.primary {{ background: #2563eb; color: #fff; border-color: #2563eb; }}
    .btn.primary:hover {{ background: #1d4ed8; }}
    .table-wrap {{ overflow: auto; max-height: 400px; }}
    table {{ border-collapse: collapse; width: 100%; white-space: nowrap; }}
    th {{
        background: #2a2a2a; color: #ccc; font-weight: 600;
        position: sticky; top: 0; z-index: 5;
    }}
    th, td {{ border: 1px solid #3a3a3a; padding: 5px 10px; text-align: left; }}
    tr:hover {{ background: #2a2a2a; }}
    td {{ max-width: 300px; overflow: hidden; text-overflow: ellipsis; }}
    .stat-card {{
        padding: 32px; text-align: center; background: #2a2a2a;
        margin: 16px; border-radius: 8px;
    }}
    .stat-value {{ font-size: 56px; font-weight: 700; color: #60a5fa; }}
    .stat-label {{ font-size: 14px; color: #aaa; margin-top: 8px; }}
    .breakdown {{ padding: 12px; }}
    .bar-row {{ display: grid; grid-template-columns: 200px 1fr 80px; gap: 8px; padding: 4px 0; align-items: center; }}
    .bar-label {{ color: #ccc; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .bar-track {{ background: #2a2a2a; border-radius: 4px; height: 18px; overflow: hidden; }}
    .bar-fill {{ background: #2563eb; height: 100%; }}
    .bar-value {{ color: #aaa; text-align: right; font-variant-numeric: tabular-nums; }}
    .cards {{ padding: 12px; display: flex; flex-direction: column; gap: 12px; }}
    .card {{ background: #2a2a2a; border-radius: 6px; padding: 12px; }}
    .card-header {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 8px; align-items: center; }}
    .card-meta {{ color: #ccc; font-size: 12px; }}
    .card-section {{ margin-top: 8px; }}
    .card-section-title {{ color: #888; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }}
    .card-section-body {{ color: #e0e0e0; white-space: pre-wrap; font-size: 13px; line-height: 1.4; margin-top: 2px; }}
    .tag {{ font-size: 10px; padding: 2px 6px; border-radius: 3px; font-weight: 600; }}
    .tag-included {{ background: #14532d; color: #86efac; }}
    .tag-excluded {{ background: #4c0519; color: #fda4af; }}
    .empty {{ padding: 32px; text-align: center; color: #888; }}
</style>
</head>
<body>
{body}
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
    function reportHeight() {{
        const h = document.documentElement.scrollHeight;
        parent.postMessage({{ type: "iframe:height", height: Math.min(h, 600) }}, "*");
    }}
    reportHeight();
    new MutationObserver(reportHeight).observe(document.body, {{ childList: true, subtree: true }});
    document.addEventListener("toggle", reportHeight, true);
</script>
</body>
</html>"""

    # ------------------------------------------------------------------
    # CSV serialization (for the Download CSV button)
    # ------------------------------------------------------------------
    @staticmethod
    def _rows_to_csv_bytes(columns: list[str], rows: list[dict]) -> bytes:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})
        return buf.getvalue().encode("utf-8")

    # ------------------------------------------------------------------
    # Event helpers
    # ------------------------------------------------------------------
    @staticmethod
    async def _emit_status(
        emitter: Optional[Callable], description: str, done: bool
    ) -> None:
        if emitter:
            await emitter(
                {
                    "type": "status",
                    "data": {"description": description, "done": done},
                }
            )
