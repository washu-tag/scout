"""
title: Scout Renderer
description: Render Scout query results as inline interactive HTML iframes (table or report flipbook). Returns (HTMLResponse, summary) so the iframe renders inline at the tool-call indicator while only a compact summary enters the LLM context.
author: Scout
version: 0.2.0
"""

import html as html_lib
import json
from typing import Any, Awaitable, Callable, Optional

from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


_IFRAME_MAX_HEIGHT = 720  # px — caps the inline iframe; content scrolls inside


def _e(value: Any) -> str:
    """HTML-escape a value, returning '—' for None/missing."""
    if value is None:
        return "&mdash;"
    if isinstance(value, (dict, list)):
        return html_lib.escape(json.dumps(value, default=str))
    return html_lib.escape(str(value))


def _summary_table_text(rows: list[dict], columns: list[str], max_sample: int) -> str:
    """Build a compact text table for the LLM: header + a few aligned sample rows."""
    n = min(max_sample, len(rows))
    header = " | ".join(columns)
    sep = "-+-".join("-" * len(c) for c in columns)
    sample = []
    for r in rows[:n]:
        cells = [str(r.get(c, "")) for c in columns]
        # Trim very long cell values so the LLM doesn't choke on full report text
        cells = [(c[:80] + "…") if len(c) > 80 else c for c in cells]
        sample.append(" | ".join(cells))
    return "\n".join([header, sep, *sample])


class Tools:
    """Render Scout query results inline (no LLM-stream bloat).

    Both methods return a 2-tuple `(HTMLResponse, summary_str)`. OWUI renders
    the HTMLResponse as a sandboxed iframe at the tool-call indicator — the
    full data is embedded as a JS const inside the iframe's HTML, so it never
    enters the LLM stream token-by-token. Only the compact summary string is
    fed back into the LLM's context for follow-up turns.

    This pattern (vs. returning a `<file type="html" id="..."/>` marker that
    OWUI would resolve via /api/v1/files/{id}/content/html) is recommended
    because:
      - No LLM cooperation needed; OWUI handles the render directly
      - No admin-only file-ownership constraint on the render endpoint
      - Less context bloat (LLM sees the summary, not the full rows again)
      - One return value, one rendering path
    """

    class Valves(BaseModel):
        max_sample_rows: int = Field(
            default=5,
            description="Number of sample rows included in the LLM context summary",
        )

    def __init__(self):
        self.valves = self.Valves()
        # OWUI inspects this; True surfaces a citation marker on the rendered output.
        self.citation = True

    # ------------------------------------------------------------------
    # Status emitter helper
    # ------------------------------------------------------------------
    @staticmethod
    async def _emit_status(
        emitter: Optional[Callable[[Any], Awaitable[None]]],
        description: str,
        done: bool,
    ) -> None:
        if emitter:
            await emitter(
                {
                    "type": "status",
                    "data": {"description": description, "done": done},
                }
            )

    # ------------------------------------------------------------------
    # render_table
    # ------------------------------------------------------------------
    async def render_table(
        self,
        rows: list[dict],
        columns: Optional[list[str]] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __user__: dict = {},
        __chat_id__: str = "",
    ) -> tuple:
        """Render rows as an inline interactive HTML table.

        Use whenever the user wants to SEE the rows themselves (vs. a count
        or aggregate). Renders inline as a sandboxed iframe with sortable
        columns and a search box. The iframe content is fully self-contained
        — data embedded as a JS const, no fetches.

        Decision rule:
          - User wants raw rows / a list / specific records   → render_table
          - User wants counts / averages / "how many" / trends → narrative,
            no tool needed
          - User wants to BROWSE individual reports          → use
            render_report_flipbook

        :param rows: list of record dicts (e.g., the rows field from a
          Trino MCP result)
        :param columns: optional ordered list of column names; defaults to
          all keys of the first row
        :return: (HTMLResponse, summary_string) — OWUI renders the iframe
          and only feeds the summary back to the LLM
        """
        if not rows:
            return ("_No results._", "_No results._")
        if not isinstance(rows, list):
            msg = f"_Expected a list of rows; got {type(rows).__name__}._"
            return (msg, msg)

        await self._emit_status(__event_emitter__, "Rendering table…", done=False)

        cols = columns or list(rows[0].keys())
        html_doc = _build_table_doc(rows, cols)
        # Lead with OWUI's canonical "embed is active" phrasing so the LLM
        # treats the embed as the displayed answer and doesn't re-dump the
        # data as text. Sample rows follow for follow-up reasoning context.
        summary = (
            f"render_table: Embedded UI result is active and visible to the "
            f"user. Do NOT re-display the data as text — the rendered table "
            f"above this message IS the user's view of the result.\n\n"
            f"Table summary: {len(rows):,} row(s) across {len(cols)} "
            f"columns: {', '.join(cols)}.\n\n"
            f"Sample ({min(self.valves.max_sample_rows, len(rows))} of "
            f"{len(rows):,} rows) — for your reasoning, not for re-display:\n"
            + _summary_table_text(rows, cols, self.valves.max_sample_rows)
        )

        await self._emit_status(__event_emitter__, "Table ready", done=True)

        return (
            HTMLResponse(
                content=html_doc,
                headers={"Content-Disposition": "inline"},
            ),
            summary,
        )

    # ------------------------------------------------------------------
    # render_report_flipbook
    # ------------------------------------------------------------------
    async def render_report_flipbook(
        self,
        reports: list[dict],
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __user__: dict = {},
        __chat_id__: str = "",
    ) -> tuple:
        """Render reports as an inline interactive flipbook with prev/next nav.

        Use when the user wants to BROWSE individual reports — intent words
        like "browse", "let me read through", "show me the reports", "page
        through them", "step through these". The flipbook displays one
        report at a time with findings, impression, and other-fields panes.

        :param reports: list of report record dicts; the most useful fields
          are epic_mrn, modality, service_name, message_dt, patient_age,
          sex, report_section_findings, report_section_impression. Extra
          fields surface in a metadata pane.
        :return: (HTMLResponse, summary_string) — OWUI renders the iframe
          and only feeds the summary back to the LLM
        """
        if not reports:
            return ("_No reports to browse._", "_No reports to browse._")
        if not isinstance(reports, list):
            msg = f"_Expected a list of reports; got {type(reports).__name__}._"
            return (msg, msg)

        await self._emit_status(__event_emitter__, "Rendering flipbook…", done=False)

        html_doc = _build_flipbook_doc(reports)

        # Pull a few orienting fields for the LLM summary
        sample = []
        for r in reports[: self.valves.max_sample_rows]:
            tag = " / ".join(
                str(r[k])
                for k in ("epic_mrn", "modality", "service_name", "message_dt")
                if r.get(k) is not None
            )
            sample.append(f"  - {tag}")
        # Lead with OWUI's canonical "embed is active" phrasing so the LLM
        # doesn't re-dump the report text as a markdown list.
        summary = (
            f"render_report_flipbook: Embedded UI result is active and "
            f"visible to the user. Do NOT re-display the reports as text — "
            f"the interactive flipbook above this message IS the user's "
            f"view of the result.\n\n"
            f"Flipbook summary: {len(reports)} report(s) navigable via "
            f"prev/next.\n\n"
            f"Orientation (first {len(sample)}) — for your reasoning, not "
            f"for re-display:\n" + "\n".join(sample)
        )

        await self._emit_status(__event_emitter__, "Flipbook ready", done=True)

        return (
            HTMLResponse(
                content=html_doc,
                headers={"Content-Disposition": "inline"},
            ),
            summary,
        )


# ----------------------------------------------------------------------------
# HTML doc builders (kept at module scope so they're not re-imported per call)
# ----------------------------------------------------------------------------


def _build_table_doc(rows: list[dict], columns: list[str]) -> str:
    rows_json = json.dumps(rows, default=str)
    cols_json = json.dumps(columns)
    return _TABLE_TMPL.replace("__ROWS__", rows_json).replace("__COLS__", cols_json)


def _build_flipbook_doc(reports: list[dict]) -> str:
    return _FLIPBOOK_TMPL.replace("__RECORDS__", json.dumps(reports, default=str))


_TABLE_TMPL = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{
    --fg: #1f2937;
    --fg-muted: #6b7280;
    --fg-subtle: #9ca3af;
    --bg: #ffffff;
    --bg-soft: #f9fafb;
    --bg-hover: #f3f4f6;
    --border: #e5e7eb;
    --accent: #2563eb;
  }}
  body {{
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    font-size: 13px;
    line-height: 1.45;
    color: var(--fg);
    background: var(--bg);
    -webkit-font-smoothing: antialiased;
  }}
  .toolbar {{
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 14px;
    background: var(--bg);
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
    z-index: 10;
  }}
  .toolbar input {{
    flex: 1;
    max-width: 320px;
    padding: 6px 10px 6px 30px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 12px;
    background: var(--bg-soft) url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%239ca3af' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><circle cx='11' cy='11' r='7'/><path d='m21 21-4.3-4.3'/></svg>") 8px center/14px no-repeat;
    transition: border-color 120ms, background-color 120ms;
  }}
  .toolbar input:focus {{
    outline: none;
    border-color: var(--accent);
    background-color: var(--bg);
  }}
  .toolbar .info {{ color: var(--fg-muted); font-size: 12px; }}
  .table-wrap {{ overflow: auto; max-height: {_IFRAME_MAX_HEIGHT - 60}px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  thead th {{
    text-align: left;
    padding: 8px 12px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--fg-muted);
    background: var(--bg-soft);
    border-bottom: 1px solid var(--border);
    cursor: pointer;
    user-select: none;
    white-space: nowrap;
    position: sticky;
    top: 0;
    z-index: 5;
  }}
  thead th:hover {{ color: var(--fg); }}
  th .arrow {{ color: var(--fg-subtle); margin-left: 4px; font-size: 10px; }}
  th.active .arrow {{ color: var(--accent); }}
  tbody td {{
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
    word-break: break-word;
    max-width: 460px;
  }}
  tbody td.expandable {{ cursor: pointer; }}
  tbody tr:nth-child(even) td {{ background: var(--bg-soft); }}
  tbody tr:hover td {{ background: var(--bg-hover); }}
  /* Long-text cells: clamp to 3 lines by default, expand on click */
  td .clamp {{
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 3;
    overflow: hidden;
  }}
  td.expanded .clamp {{ -webkit-line-clamp: unset; }}
  td .more-hint {{
    color: var(--accent);
    font-size: 11px;
    margin-top: 2px;
    user-select: none;
  }}
  td.expanded .more-hint::after {{ content: "show less"; }}
  td:not(.expanded) .more-hint::after {{ content: "show more"; }}
  .empty {{ color: var(--fg-subtle); font-style: italic; }}
</style>
</head>
<body>
<div class="toolbar">
  <input id="q" type="search" placeholder="Filter rows…" />
  <span class="info" id="footer"></span>
</div>
<div class="table-wrap">
  <table id="t"><thead><tr id="head"></tr></thead><tbody id="body"></tbody></table>
</div>
<script>
const ROWS = __ROWS__;
const COLS = __COLS__;
const CLAMP_THRESHOLD = 140; // chars — cells longer than this get clamp + expand affordance
let sortCol = null, sortDir = 1, filter = "";

function fmt(v) {{ if (v === null || v === undefined) return ""; if (typeof v === "object") return JSON.stringify(v); return String(v); }}
function compare(a, b) {{
  if (a == null) return 1; if (b == null) return -1;
  const na = Number(a), nb = Number(b);
  if (!isNaN(na) && !isNaN(nb)) return (na - nb) * sortDir;
  return String(a).localeCompare(String(b)) * sortDir;
}}
function renderHead() {{
  const tr = document.getElementById("head");
  tr.innerHTML = "";
  COLS.forEach((c) => {{
    const th = document.createElement("th");
    th.textContent = c;
    if (sortCol === c) th.classList.add("active");
    const arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.textContent = sortCol === c ? (sortDir > 0 ? "▲" : "▼") : "↕";
    th.appendChild(arrow);
    th.addEventListener("click", () => {{
      if (sortCol === c) sortDir = -sortDir; else {{ sortCol = c; sortDir = 1; }}
      render();
    }});
    tr.appendChild(th);
  }});
}}
function render() {{
  let rows = ROWS;
  if (filter) {{
    const f = filter.toLowerCase();
    rows = rows.filter(r => COLS.some(c => fmt(r[c]).toLowerCase().includes(f)));
  }}
  if (sortCol) rows = [...rows].sort((a, b) => compare(a[sortCol], b[sortCol]));
  renderHead();
  const body = document.getElementById("body");
  body.innerHTML = "";
  rows.forEach((r) => {{
    const tr = document.createElement("tr");
    COLS.forEach((c) => {{
      const td = document.createElement("td");
      const v = fmt(r[c]);
      if (v === "") {{
        td.innerHTML = '<span class="empty">—</span>';
      }} else if (v.length > CLAMP_THRESHOLD) {{
        // Long content: clamp to 3 lines with toggle
        const clamp = document.createElement("div");
        clamp.className = "clamp";
        clamp.textContent = v;
        const hint = document.createElement("div");
        hint.className = "more-hint";
        td.classList.add("expandable");
        td.appendChild(clamp);
        td.appendChild(hint);
        td.addEventListener("click", () => {{
          td.classList.toggle("expanded");
          reportHeight();
        }});
      }} else {{
        td.textContent = v;
      }}
      tr.appendChild(td);
    }});
    body.appendChild(tr);
  }});
  const footer = document.getElementById("footer");
  footer.textContent = (filter || sortCol)
    ? `${{rows.length}} of ${{ROWS.length}} rows` : `${{ROWS.length}} row(s)`;
  reportHeight();
}}
document.getElementById("q").addEventListener("input", (e) => {{ filter = e.target.value; render(); }});
// Send height to parent multiple times to defeat the mount-race with
// FullHeightIframe's onMessage listener: a single postMessage may fire
// before the parent component mounts → message lost → iframe stuck at
// browser default (~150px). Force=true bypasses the dedup so retries
// always send even if the height hasn't changed.
let _lastReportedHeight = -1;
function reportHeight(force) {{
  const h = Math.min(document.documentElement.scrollHeight, {_IFRAME_MAX_HEIGHT});
  if (!force && h === _lastReportedHeight) return;
  _lastReportedHeight = h;
  parent.postMessage({{ type: "iframe:height", height: h }}, "*");
}}
// Burst of resends after initial render: 0ms (now), 50ms, 200ms, 600ms.
// 0ms = best case parent already mounted; 50/200ms = catch mount-race;
// 600ms = catch font-load reflows + slow paints.
[0, 50, 200, 600].forEach((d) => setTimeout(() => reportHeight(true), d));
new MutationObserver(() => reportHeight()).observe(document.body, {{ childList: true, subtree: true, characterData: true }});
window.addEventListener("resize", () => reportHeight());
if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => reportHeight(true));
render();
</script>
</body>
</html>
"""


_FLIPBOOK_TMPL = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; color: #111; background: #fff; }}
  #flipbook {{ display: flex; flex-direction: column; min-height: 480px; max-height: {_IFRAME_MAX_HEIGHT}px; }}
  header {{ padding: 10px 14px; border-bottom: 1px solid #e5e7eb; background: #fafafa; }}
  .counter {{ font-size: 12px; color: #6b7280; }}
  .meta {{ font-size: 14px; font-weight: 600; margin-top: 4px; }}
  main {{ flex: 1; padding: 10px 14px; overflow-y: auto; }}
  section {{ margin-bottom: 14px; }}
  section h3 {{ margin: 0 0 6px 0; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: #6b7280; }}
  pre {{ white-space: pre-wrap; font-family: ui-monospace, monospace; font-size: 13px; background: #f9fafb; padding: 10px; border-radius: 6px; margin: 0; max-height: 240px; overflow-y: auto; }}
  dl {{ font-size: 13px; margin: 0; display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; }}
  dt {{ color: #6b7280; }}
  dd {{ margin: 0; word-break: break-word; }}
  footer {{ padding: 8px 14px; border-top: 1px solid #e5e7eb; display: flex; gap: 8px; background: #fafafa; }}
  button {{ padding: 5px 12px; border: 1px solid #d1d5db; background: white; border-radius: 6px; cursor: pointer; font-size: 13px; }}
  button:hover:not(:disabled) {{ background: #f3f4f6; }}
  button:disabled {{ opacity: 0.4; cursor: not-allowed; }}
</style>
</head>
<body>
<div id="flipbook">
  <header>
    <div class="counter"><span id="idx">1</span> / <span id="total">0</span></div>
    <div class="meta" id="meta"></div>
  </header>
  <main>
    <section><h3>Findings</h3><pre id="findings"></pre></section>
    <section><h3>Impression</h3><pre id="impression"></pre></section>
    <section><h3>Other fields</h3><dl id="extra"></dl></section>
  </main>
  <footer>
    <button id="prev">← Previous</button>
    <button id="next">Next →</button>
  </footer>
</div>
<script>
const RECORDS = __RECORDS__;
// Field-name fallback chains — the LLM sometimes aliases columns in SQL
// (SELECT report_section_impression AS impression). Try canonical names
// first, fall back to common shortened forms.
const FINDINGS_KEYS = ['report_section_findings', 'findings', 'report_findings'];
const IMPRESSION_KEYS = ['report_section_impression', 'impression', 'report_impression'];
const MRN_KEYS = ['epic_mrn', 'mrn', 'patient_id'];
const DATE_KEYS = ['message_dt', 'timestamp', 'date', 'report_date'];
// Header metadata we surface on each card (in this order). Renders with
// the first non-null value found per axis.
const META_AXES = [
  MRN_KEYS,
  ['modality'],
  ['service_name', 'service'],
  DATE_KEYS,
  ['patient_age', 'age'],
  ['sex', 'gender'],
];
// Keys we hide from the "Other fields" pane (covers all the fallback
// chains so we never show the same data twice).
const HIDE = new Set([
  ...FINDINGS_KEYS, ...IMPRESSION_KEYS,
  ...MRN_KEYS, ...DATE_KEYS,
  ...META_AXES.flat(),
  'report_text',
]);
let idx = 0;
function fmt(v) {{ if (v == null) return ''; if (typeof v === 'object') return JSON.stringify(v); return String(v); }}
function pick(record, keys) {{
  for (const k of keys) {{
    if (record[k] != null && record[k] !== '') return record[k];
  }}
  return null;
}}
function render() {{
  const r = RECORDS[idx] || {{}};
  document.getElementById('idx').textContent = idx + 1;
  document.getElementById('total').textContent = RECORDS.length;
  document.getElementById('meta').textContent =
    META_AXES.map(keys => {{ const v = pick(r, keys); return v != null ? fmt(v) : null; }})
      .filter(Boolean).join(' • ') || '(no metadata)';
  document.getElementById('findings').textContent = pick(r, FINDINGS_KEYS) || '(none)';
  document.getElementById('impression').textContent = pick(r, IMPRESSION_KEYS) || '(none)';
  const extra = document.getElementById('extra');
  extra.innerHTML = '';
  Object.keys(r).filter(k => !HIDE.has(k)).forEach(k => {{
    const dt = document.createElement('dt'); dt.textContent = k;
    const dd = document.createElement('dd'); dd.textContent = fmt(r[k]);
    extra.appendChild(dt); extra.appendChild(dd);
  }});
  document.getElementById('prev').disabled = idx === 0;
  document.getElementById('next').disabled = idx >= RECORDS.length - 1;
  reportHeight();
}}
document.getElementById('prev').addEventListener('click', () => {{ if (idx > 0) {{ idx--; render(); }} }});
document.getElementById('next').addEventListener('click', () => {{ if (idx < RECORDS.length - 1) {{ idx++; render(); }} }});
document.addEventListener('keydown', (e) => {{
  if (e.key === 'ArrowLeft' && idx > 0) {{ idx--; render(); }}
  if (e.key === 'ArrowRight' && idx < RECORDS.length - 1) {{ idx++; render(); }}
}});
// Send height to parent multiple times to defeat the mount-race with
// FullHeightIframe's onMessage listener: a single postMessage may fire
// before the parent component mounts → message lost → iframe stuck at
// browser default (~150px). Force=true bypasses the dedup so retries
// always send even if the height hasn't changed.
let _lastReportedHeight = -1;
function reportHeight(force) {{
  const h = Math.min(document.documentElement.scrollHeight, {_IFRAME_MAX_HEIGHT});
  if (!force && h === _lastReportedHeight) return;
  _lastReportedHeight = h;
  parent.postMessage({{ type: "iframe:height", height: h }}, "*");
}}
// Burst of resends after initial render: 0ms (now), 50ms, 200ms, 600ms.
// 0ms = best case parent already mounted; 50/200ms = catch mount-race;
// 600ms = catch font-load reflows + slow paints.
[0, 50, 200, 600].forEach((d) => setTimeout(() => reportHeight(true), d));
new MutationObserver(() => reportHeight()).observe(document.body, {{ childList: true, subtree: true, characterData: true }});
window.addEventListener("resize", () => reportHeight());
if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => reportHeight(true));
render();
</script>
</body>
</html>
"""
