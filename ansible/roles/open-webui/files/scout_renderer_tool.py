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


# ----------------------------------------------------------------------------
# Send-to-XNAT button + modal: shared chunks injected into both the table and
# flipbook templates so the user can hand off the cohort they're looking at
# without leaving the iframe. The "send" is a client-side mock — there's no
# real XNAT instance to POST to from a sandboxed srcdoc iframe (null origin
# would block CORS anyway). The mock generates a plausible response on
# button click after a brief simulated delay.
# ----------------------------------------------------------------------------

_XNAT_BUTTON_CSS = """
  .xnat-btn {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 5px 10px; border: 1px solid #d1d5db;
    background: #fff; color: #1f2937;
    border-radius: 6px; cursor: pointer;
    font-size: 12px; font-weight: 500;
  }
  .xnat-btn:hover { background: #f3f4f6; border-color: #9ca3af; }
  .xnat-btn svg { flex-shrink: 0; }
  .xnat-overlay {
    display: none;
    position: fixed; inset: 0;
    background: rgba(15, 23, 42, 0.45);
    align-items: center; justify-content: center;
    z-index: 9999;
  }
  .xnat-overlay.open { display: flex; }
  .xnat-modal {
    background: #fff; color: #1f2937;
    padding: 20px 22px; border-radius: 10px;
    width: min(560px, calc(100vw - 32px));
    max-height: calc(100vh - 32px); overflow-y: auto;
    box-shadow: 0 12px 32px rgba(15, 23, 42, 0.18);
    font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
    font-size: 13px; line-height: 1.5;
  }
  .xnat-modal h3 { margin: 0 0 6px; font-size: 16px; font-weight: 600; }
  .xnat-modal .sub { color: #6b7280; font-size: 12px; margin-bottom: 14px; }
  .xnat-modal label {
    display: block; margin: 10px 0 4px;
    font-weight: 600; font-size: 12px; color: #374151;
  }
  .xnat-modal input[type=text], .xnat-modal textarea {
    width: 100%; padding: 7px 9px;
    border: 1px solid #d1d5db; border-radius: 6px;
    background: #fff; color: #1f2937;
    font-size: 13px; font-family: inherit; box-sizing: border-box;
  }
  .xnat-modal textarea { min-height: 56px; resize: vertical; }
  .xnat-modal input:focus, .xnat-modal textarea:focus {
    outline: none; border-color: #2563eb; box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.15);
  }
  .xnat-modal .toggle-link {
    background: none; border: none; padding: 4px 0;
    color: #2563eb; cursor: pointer; font-size: 12px;
  }
  .xnat-modal .info-block {
    background: #f9fafb; border: 1px solid #e5e7eb;
    border-radius: 6px; padding: 10px 12px; margin: 10px 0;
    font-size: 12px;
  }
  .xnat-modal .info-block div { padding: 2px 0; }
  .xnat-modal pre {
    background: #f9fafb; border: 1px solid #e5e7eb;
    border-radius: 6px; padding: 8px 10px; margin: 6px 0 0;
    font-size: 11px; font-family: ui-monospace, monospace;
    max-height: 180px; overflow: auto; white-space: pre-wrap;
  }
  .xnat-modal .btn-row {
    display: flex; gap: 8px; justify-content: flex-end;
    margin-top: 16px;
  }
  .xnat-modal button.action {
    padding: 7px 14px; border-radius: 6px; cursor: pointer;
    font-size: 13px; font-weight: 500; border: 1px solid #d1d5db;
    background: #fff; color: #1f2937;
  }
  .xnat-modal button.action:hover { background: #f3f4f6; }
  .xnat-modal button.action.primary {
    background: #2563eb; color: #fff; border-color: #2563eb;
  }
  .xnat-modal button.action.primary:hover { background: #1d4ed8; }
  .xnat-modal .demo-note {
    color: #6b7280; font-size: 11px; font-style: italic; margin: 12px 0 0;
  }
  .xnat-spinner {
    width: 14px; height: 14px;
    border: 2px solid #e5e7eb; border-top-color: #2563eb;
    border-radius: 50%; display: inline-block;
    vertical-align: middle; margin-right: 8px;
    animation: xnat-spin 0.7s linear infinite;
  }
  @keyframes xnat-spin { to { transform: rotate(360deg); } }
"""

# SVG XNAT swoosh logo (greyscale), inlined for the button icon. Goes in the
# template's toolbar/footer slot.
_XNAT_BUTTON_HTML = """
<button id="xnat-open-btn" class="xnat-btn" type="button" title="Send this cohort to XNAT">
  <svg width="14" height="14" viewBox="0 0 320 320" aria-hidden="true">
    <path d="M0 0 C-4.09 8.17 -11.84 14.78 -18.12 21.38 C-29.22 34.18 -38.12 48.38 -38.19 65.75 C-38.2 67.29 -38.22 68.83 -38.23 70.42 C-38.01 74.88 -37.24 78.72 -36 83 C-31.92 79.55 -27.96 76.03 -24.06 72.38 C0.53 50.03 49.43 14 84 14 C83.42 16.23 82.85 18.45 82.25 20.75 C76.52 43.77 72.56 66.91 73.25 90.71 C73.28 92.61 73.31 94.52 73.34 96.48 C73.4 100.44 73.53 104.39 73.71 108.34 C73.79 122.39 73.79 122.39 68.76 126.91 C64.58 129 60.44 130.53 56 132 C44.59 137.91 33.23 143.97 22.31 150.75 C21.01 151.55 19.71 152.35 18.36 153.17 C16 156 16 156 16.95 160.34 C17.28 161.11 17.28 161.11 19 165 C36.54 217.74 17.48 267.38 -18.06 307.75 C-28.64 319 -28.64 319 -35 319 C-37.36 316.25 -37.36 316.25 -39.75 312.44 C-40.62 311.06 -41.49 309.69 -42.38 308.27 C-43.25 306.86 -44.11 305.45 -45 304 C-45.78 302.75 -46.56 301.51 -47.37 300.23 C-63.28 274.4 -78 242.87 -78 212 C-79.65 211.34 -81.3 210.68 -83 210 C-99 202 -99 202 -102.94 200 C-111.12 195.97 -119.2 192.55 -127.87 189.73 C-129.62 189.12 -131.38 188.52 -133.19 187.9 C-136.76 186.68 -140.36 185.53 -143.98 184.46 C-152.37 181.52 -152.37 181.52 -156 178 C-155.76 171.33 -152.79 165.96 -150 160 C-145.02 140.08 -142.1 120.47 -141 100 C-117.66 109.34 -94.24 127.97 -75 144 C-74.34 144 -73.68 144 -73 144 C-71.25 140.81 -71.25 140.81 -71 136 C-73.24 132.82 -75.53 129.65 -78.02 126.66 C-103.53 95.37 -121 52.99 -109 13 C-108.67 12.34 -108.34 11.68 -108 11 C-102.07 10.76 -96.18 10.63 -90.25 10.56 C-62.04 10.02 -34.48 7.07 -6.89 0.85 C-3 0 -3 0 0 0 Z" fill="#6b7280" transform="translate(196,1)"/>
  </svg>
  Send to XNAT
</button>
"""

# Modal overlay markup; injected at the end of body (sibling of toolbar/main
# content) so position:fixed positioning isn't perturbed by sticky/transformed
# ancestors in the toolbar.
_XNAT_OVERLAY_HTML = """
<div id="xnat-overlay" class="xnat-overlay" role="dialog" aria-modal="true" aria-hidden="true">
  <div class="xnat-modal" id="xnat-modal">
    <!-- body filled in dynamically by the modal JS -->
  </div>
</div>
"""

_XNAT_MODAL_JS = """
// In-iframe Send to XNAT modal. The iframe ALWAYS POSTs to BRIDGE_URL when
// it's set; the bridge service is the trust boundary that holds XNAT
// credentials and translates this request into a real XNAT IQ call. When
// BRIDGE_URL is empty (current state — the bridge service hasn't been built
// yet), the modal generates a plausible response client-side so the demo UX
// is complete without any backend dependency. Switching to "real" later is
// a one-line valve change; the iframe code never moves.
//
// USER is captured by the Tool at render time (it has access to __user__ in
// OWUI's backend) and embedded as a JS const so the bridge knows who
// requested the export. The iframe itself can't authenticate to OWUI from
// its null origin, which is why we capture identity server-side.
//
// Cohort context (ROWS/COLS for the table template, RECORDS for flipbook)
// is also embedded as JS consts; the modal pulls a row count from whichever
// is defined.
(function () {
  const overlay = document.getElementById('xnat-overlay');
  const modal = document.getElementById('xnat-modal');
  let formState = { project: '', irb: '', comment: '', rationale: '', duc: '' };

  function cohortPayload() {
    if (typeof ROWS !== 'undefined' && Array.isArray(ROWS)) {
      return { count: ROWS.length, label: 'rows', rows: ROWS };
    }
    if (typeof RECORDS !== 'undefined' && Array.isArray(RECORDS)) {
      return { count: RECORDS.length, label: 'reports', rows: RECORDS };
    }
    return { count: 0, label: 'rows', rows: [] };
  }

  function escape(s) {
    const t = document.createElement('span');
    t.textContent = s == null ? '' : String(s);
    return t.innerHTML;
  }

  function buildRequestPayload() {
    const cohort = cohortPayload();
    return {
      projectName: formState.project,
      irbNumber: formState.irb || null,
      comment: formState.comment || null,
      rationale: formState.rationale || null,
      dataUseCommitteeOversight: formState.duc || null,
      requestUser: USER && (USER.username || USER.email || USER.name) || 'unknown',
      user: USER || null,
      // The iframe holds the cohort, so it sends it. The bridge dedupes
      // and translates to whatever XNAT expects (typically accession_number).
      data: cohort.rows,
      itemCount: cohort.count,
    };
  }

  function openModal() {
    overlay.classList.add('open');
    overlay.setAttribute('aria-hidden', 'false');
    // Modal is position:fixed inside the iframe; if the iframe is currently
    // shorter than the modal needs, ask the parent to grow the iframe so the
    // modal isn't cut off. Restored on close via reportHeight().
    parent.postMessage({ type: 'iframe:height', height: __IFRAME_MAX_HEIGHT__ }, '*');
    renderForm();
  }

  function closeModal() {
    overlay.classList.remove('open');
    overlay.setAttribute('aria-hidden', 'true');
    if (typeof reportHeight === 'function') reportHeight(true);
  }

  function renderForm() {
    const cohort = cohortPayload();
    const demoBanner = BRIDGE_URL
      ? ''
      : '<p class="demo-note">Demo mode &mdash; bridge URL not configured, response is generated client-side.</p>';
    modal.innerHTML = `
      <h3>Send to XNAT</h3>
      <p class="sub">Submit ${cohort.count} ${cohort.label} from this result as an XNAT data request.</p>
      <label for="xnat-project">XNAT project name (required)</label>
      <input type="text" id="xnat-project" placeholder="e.g. MyProject" value="${escape(formState.project)}" />
      <button class="toggle-link" id="xnat-toggle-optional" type="button">▶ Optional details</button>
      <div id="xnat-optional" style="display:none;">
        <label for="xnat-irb">IRB number</label>
        <input type="text" id="xnat-irb" placeholder="e.g. 202400123" value="${escape(formState.irb)}" />
        <label for="xnat-comment">Comment</label>
        <input type="text" id="xnat-comment" value="${escape(formState.comment)}" />
        <label for="xnat-rationale">Rationale</label>
        <input type="text" id="xnat-rationale" value="${escape(formState.rationale)}" />
        <label for="xnat-duc">Data Use Committee oversight</label>
        <input type="text" id="xnat-duc" value="${escape(formState.duc)}" />
      </div>
      ${demoBanner}
      <div class="btn-row">
        <button class="action" id="xnat-cancel" type="button">Cancel</button>
        <button class="action primary" id="xnat-send" type="button">Send</button>
      </div>
    `;

    let optOpen = !!(formState.irb || formState.comment || formState.rationale || formState.duc);
    const optDiv = modal.querySelector('#xnat-optional');
    const optBtn = modal.querySelector('#xnat-toggle-optional');
    function syncOptional() {
      optDiv.style.display = optOpen ? 'block' : 'none';
      optBtn.textContent = (optOpen ? '▼ ' : '▶ ') + 'Optional details';
    }
    syncOptional();
    optBtn.onclick = () => { optOpen = !optOpen; syncOptional(); };

    modal.querySelector('#xnat-cancel').onclick = closeModal;
    modal.querySelector('#xnat-send').onclick = onSubmit;
    setTimeout(() => modal.querySelector('#xnat-project').focus(), 0);
  }

  async function onSubmit() {
    const project = modal.querySelector('#xnat-project').value.trim();
    if (!project) {
      alert('Project name is required.');
      modal.querySelector('#xnat-project').focus();
      return;
    }
    formState = {
      project,
      irb: modal.querySelector('#xnat-irb').value.trim(),
      comment: modal.querySelector('#xnat-comment').value.trim(),
      rationale: modal.querySelector('#xnat-rationale').value.trim(),
      duc: modal.querySelector('#xnat-duc').value.trim(),
    };
    renderSubmitting();
    const payload = buildRequestPayload();

    if (BRIDGE_URL) {
      // Real path — POST to bridge service. Bridge holds XNAT creds and
      // returns the XNAT IQ response (or an error). Two cross-origin gates
      // to satisfy when wiring this up:
      //   1. CSP: srcdoc iframes inherit the parent's connect-src. OWUI's
      //      Traefik csp-middleware sets connect-src 'self' (see ADR 0009);
      //      put the bridge under chat.<host>/<path> so 'self' covers it,
      //      OR add the bridge host explicitly to connect-src.
      //   2. CORS: srcdoc iframes have null origin, so the bridge must
      //      respond Access-Control-Allow-Origin: '*' (no credentials).
      //      User identity travels in the body (USER captured at render
      //      time by the Tool, server-side).
      try {
        const resp = await fetch(BRIDGE_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
          mode: 'cors',
          credentials: 'omit',
        });
        const body = await resp.json().catch(() => ({}));
        renderResult({
          payload,
          response: body,
          httpStatus: resp.status,
          error: resp.ok ? null : `Bridge returned HTTP ${resp.status}`,
        });
      } catch (e) {
        renderResult({
          payload,
          response: null,
          httpStatus: null,
          error: `Network error: ${e.message || e}`,
        });
      }
      return;
    }

    // Demo path — fabricate the response. Wait briefly so the spinner is
    // perceptible; production fetch latency replaces this naturally.
    await new Promise((r) => setTimeout(r, 1200));
    const requestId = 1000 + Math.floor(Math.random() * 9000);
    renderResult({
      payload,
      response: {
        id: requestId,
        status: 'PRE_PROCESSING',
        deidStatus: 'IDENTIFIABLE',
        projectName: payload.projectName,
        irbNumber: payload.irbNumber,
        comment: payload.comment,
        requestUser: payload.requestUser,
        numberOfItemsRequested: payload.itemCount,
        message: 'Request is being pre-processed.',
        _mock: true,
      },
      httpStatus: 202,
      error: null,
    });
  }

  function renderSubmitting() {
    modal.innerHTML = `
      <h3><span class="xnat-spinner"></span>Submitting to XNAT&hellip;</h3>
      <p class="sub">Recording your data request.</p>
    `;
  }

  function renderResult({ payload, response, httpStatus, error }) {
    const cohort = cohortPayload();
    const optionalLines = [];
    if (formState.irb) optionalLines.push(`<div><strong>IRB number:</strong> ${escape(formState.irb)}</div>`);
    if (formState.comment) optionalLines.push(`<div><strong>Comment:</strong> ${escape(formState.comment)}</div>`);
    if (formState.rationale) optionalLines.push(`<div><strong>Rationale:</strong> ${escape(formState.rationale)}</div>`);
    if (formState.duc) optionalLines.push(`<div><strong>Data Use Committee:</strong> ${escape(formState.duc)}</div>`);

    const isError = !!error;
    const isMock = response && response._mock;
    const heading = isError
      ? 'XNAT submission failed'
      : (isMock ? 'Sent to XNAT &mdash; demo' : 'Sent to XNAT');
    const reqId = response && response.id;
    const status = response && response.status;
    const note = isError
      ? `<p class="demo-note" style="color:#b91c1c;">${escape(error)}</p>`
      : (isMock
        ? '<p class="demo-note">No actual transfer occurred. Set the bridge_url valve on Scout Renderer (Workspace &rsaquo; Tools) to flip this on once the bridge service exists.</p>'
        : '');

    modal.innerHTML = `
      <h3>${heading}</h3>
      <div class="info-block">
        <div><strong>Project:</strong> ${escape(formState.project)}</div>
        <div><strong>${escape(cohort.label.charAt(0).toUpperCase() + cohort.label.slice(1))} sent:</strong> ${cohort.count}</div>
        ${reqId != null ? `<div><strong>Request ID:</strong> ${escape(String(reqId))}</div>` : ''}
        ${status ? `<div><strong>Status:</strong> ${escape(String(status))}</div>` : ''}
        ${httpStatus != null ? `<div><strong>HTTP:</strong> ${httpStatus}</div>` : ''}
        ${optionalLines.join('')}
      </div>
      <label>Request payload</label>
      <pre>${escape(JSON.stringify(payload, null, 2))}</pre>
      ${response != null ? `<label>Response</label><pre>${escape(JSON.stringify(response, null, 2))}</pre>` : ''}
      ${note}
      <div class="btn-row">
        <button class="action primary" id="xnat-done" type="button">Done</button>
      </div>
    `;
    modal.querySelector('#xnat-done').onclick = closeModal;
  }

  document.getElementById('xnat-open-btn').addEventListener('click', openModal);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && overlay.classList.contains('open')) closeModal();
  });
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeModal(); });
})();
""".replace(
    "__IFRAME_MAX_HEIGHT__", str(_IFRAME_MAX_HEIGHT)
)


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
        bridge_url: str = Field(
            default="",
            description=(
                "Full URL the iframe's Send-to-XNAT modal POSTs to. The bridge "
                "is the trust boundary that holds XNAT credentials and "
                "translates this request into a real XNAT IQ data request. "
                "Leave empty to use a client-side mock response (current "
                "demo state — no bridge service deployed yet). When set, the "
                "iframe POSTs the cohort + form metadata + user identity here "
                "from a null-origin sandbox; the endpoint must serve "
                "permissive CORS (Access-Control-Allow-Origin: *)."
            ),
        )

    def __init__(self):
        self.valves = self.Valves()
        # OWUI inspects this; True surfaces a citation marker on the rendered output.
        self.citation = True

    @staticmethod
    def _user_info(user: dict | None) -> dict | None:
        """Slim user dict for embedding in the iframe — never leak password
        hashes, API keys, or settings. The bridge service uses this to know
        who requested the export."""
        if not user:
            return None
        return {
            "id": user.get("id") or "",
            "name": user.get("name") or "",
            "email": user.get("email") or "",
            "username": user.get("username") or "",
            "role": user.get("role") or "",
        }

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
        html_doc = _build_table_doc(
            rows,
            cols,
            self._user_info(__user__),
            self.valves.bridge_url,
        )
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

        html_doc = _build_flipbook_doc(
            reports,
            self._user_info(__user__),
            self.valves.bridge_url,
        )

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


def _build_table_doc(
    rows: list[dict],
    columns: list[str],
    user: dict | None,
    bridge_url: str,
) -> str:
    rows_json = json.dumps(rows, default=str)
    cols_json = json.dumps(columns)
    return (
        _TABLE_TMPL.replace("__ROWS__", rows_json)
        .replace("__COLS__", cols_json)
        .replace("__USER__", json.dumps(user or None))
        .replace("__BRIDGE_URL__", json.dumps(bridge_url or ""))
    )


def _build_flipbook_doc(
    reports: list[dict],
    user: dict | None,
    bridge_url: str,
) -> str:
    return (
        _FLIPBOOK_TMPL.replace("__RECORDS__", json.dumps(reports, default=str))
        .replace("__USER__", json.dumps(user or None))
        .replace("__BRIDGE_URL__", json.dumps(bridge_url or ""))
    )


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
  .toolbar .spacer {{ flex: 1; }}
{_XNAT_BUTTON_CSS}
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
  <span class="spacer"></span>
  {_XNAT_BUTTON_HTML}
</div>
<div class="table-wrap">
  <table id="t"><thead><tr id="head"></tr></thead><tbody id="body"></tbody></table>
</div>
<script>
const ROWS = __ROWS__;
const COLS = __COLS__;
const USER = __USER__;
const BRIDGE_URL = __BRIDGE_URL__;
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
{_XNAT_OVERLAY_HTML}
<script>{_XNAT_MODAL_JS}</script>
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
  footer {{ padding: 8px 14px; border-top: 1px solid #e5e7eb; display: flex; gap: 8px; background: #fafafa; align-items: center; }}
  footer .spacer {{ flex: 1; }}
  /* Default <button> styling for prev/next, scoped so xnat-btn keeps its own look. */
  footer > button {{ padding: 5px 12px; border: 1px solid #d1d5db; background: white; border-radius: 6px; cursor: pointer; font-size: 13px; }}
  footer > button:hover:not(:disabled) {{ background: #f3f4f6; }}
  footer > button:disabled {{ opacity: 0.4; cursor: not-allowed; }}
{_XNAT_BUTTON_CSS}
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
    <span class="spacer"></span>
    {_XNAT_BUTTON_HTML}
  </footer>
</div>
<script>
const RECORDS = __RECORDS__;
const USER = __USER__;
const BRIDGE_URL = __BRIDGE_URL__;
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
{_XNAT_OVERLAY_HTML}
<script>{_XNAT_MODAL_JS}</script>
</body>
</html>
"""
