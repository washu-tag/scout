"""
title: Scout Renderer
description: Execute SQL against Scout's Trino and render the rows as an inline interactive HTML iframe — a sortable, filterable, paginated table where any row click opens a focused detail modal with full Findings / Impression / metadata and arrow-key navigation through the cohort. The Tool runs the query itself; the LLM passes a SELECT, NOT rows. Iframe ships the cohort's full accession_number list (from a separate wrapper query) to XNAT regardless of how many rows display. Returns (HTMLResponse, summary) so the iframe renders inline while only row count, column names, sample rows, and any SQL error feed back to the LLM.
author: Scout
version: 0.4.0
requirements: trino
"""

import html as html_lib
import json
from typing import Any, Awaitable, Callable, Optional

from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


_IFRAME_MAX_HEIGHT = 720  # px — caps the inline iframe; content scrolls inside


# ----------------------------------------------------------------------------
# Send-to-XNAT button + modal: chunks injected into the table template so the
# user can hand off the cohort they're looking at without leaving the iframe.
# When BRIDGE_URL is empty (current state — no bridge service deployed yet)
# the modal generates a plausible response client-side after a brief
# simulated delay. When set, the iframe POSTs to the bridge.
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
  .xnat-modal .result-grid {
    display: grid; grid-template-columns: max-content 1fr;
    gap: 6px 16px; margin: 14px 0 4px;
    background: #f9fafb; border: 1px solid #e5e7eb;
    border-radius: 6px; padding: 12px 14px;
    font-size: 13px;
  }
  .xnat-modal .result-grid > div {
    display: contents;
  }
  .xnat-modal .result-grid strong {
    color: #6b7280; font-weight: 500; font-size: 12px;
    text-transform: uppercase; letter-spacing: 0.04em;
    align-self: center;
  }
  .xnat-modal .result-grid span {
    color: #1f2937; word-break: break-word;
  }
  .xnat-modal .next-steps {
    margin: 4px 0 8px 22px;
    padding: 0;
    color: #4b5563;
    font-size: 13px;
    line-height: 1.6;
  }
  .xnat-modal .next-steps li { margin: 2px 0; }
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

  // The cohort sent to XNAT is the full SELECT-derived accession_number
  // list (ALL_ACCESSIONS from the Tool's secondary query), not just the
  // currently-displayed rows. Pagination / filter / sort shape what's on
  // screen but doesn't change what ships.
  function cohortPayload() {
    if (typeof ALL_ACCESSIONS !== 'undefined' &&
        Array.isArray(ALL_ACCESSIONS) &&
        ALL_ACCESSIONS.length > 0) {
      return {
        count: ALL_ACCESSIONS.length,
        label: 'reports',
        accessions: ALL_ACCESSIONS,
      };
    }
    // Fallback: extract accessions from the displayed rows
    if (typeof ROWS !== 'undefined' && Array.isArray(ROWS)) {
      const acc = ROWS.map((r) => r && r.accession_number).filter(Boolean);
      return { count: acc.length, label: 'reports', accessions: acc };
    }
    return { count: 0, label: 'reports', accessions: [] };
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
      // Just accession numbers — the bridge resolves them to studies.
      // Mirrors the XNAT IQ data-request shape (`data: [{"Accession Number": ...}]`).
      data: cohort.accessions.map((a) => ({ 'Accession Number': a })),
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

    // No bridge yet — fabricate the response so the result page renders.
    // Brief delay so the spinner is perceptible (production fetch latency
    // replaces this naturally once BRIDGE_URL is set).
    await new Promise((r) => setTimeout(r, 1200));
    const requestId = 1000 + Math.floor(Math.random() * 9000);
    renderResult({
      payload,
      response: {
        id: requestId,
        status: 'PENDING_APPROVAL',
        deidStatus: 'IDENTIFIABLE',
        projectName: payload.projectName,
        irbNumber: payload.irbNumber,
        comment: payload.comment,
        requestUser: payload.requestUser,
        numberOfItemsRequested: payload.itemCount,
        message: 'Request submitted for approval.',
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

  // Map raw XNAT IQ status codes to user-friendly phrasing. Falls through to
  // the raw value if we don't have a friendly version yet.
  const STATUS_TEXT = {
    PENDING_APPROVAL: 'Pending approval',
    APPROVED: 'Approved',
    REJECTED: 'Rejected',
    PRE_PROCESSING: 'Pre-processing',
    PROCESSING: 'Processing',
    READY_FOR_DOWNLOAD: 'Ready for download',
  };

  function renderResult({ payload, response, httpStatus, error }) {
    const cohort = cohortPayload();

    if (error) {
      modal.innerHTML = `
        <h3 style="color:#b91c1c;">Submission failed</h3>
        <p class="sub">${escape(error)}</p>
        <p class="sub">No data was transferred. You can retry from the table below.</p>
        <div class="btn-row">
          <button class="action primary" id="xnat-done" type="button">Close</button>
        </div>
      `;
      modal.querySelector('#xnat-done').onclick = closeModal;
      return;
    }

    const reqId = response && response.id;
    const rawStatus = (response && response.status) || 'PENDING_APPROVAL';
    const friendlyStatus = STATUS_TEXT[rawStatus] || rawStatus;
    const recipientEmail = (USER && USER.email) || '';
    const requestUrl = (XNAT_EXTERNAL_URL && reqId != null)
      ? XNAT_EXTERNAL_URL.replace(/\\/$/, '') + '/xapi/iq/data-requests/' + encodeURIComponent(reqId)
      : '';

    const optionalLines = [];
    if (formState.irb) optionalLines.push(`<div><strong>IRB number</strong><span>${escape(formState.irb)}</span></div>`);
    if (formState.rationale) optionalLines.push(`<div><strong>Rationale</strong><span>${escape(formState.rationale)}</span></div>`);
    if (formState.duc) optionalLines.push(`<div><strong>Data Use Committee</strong><span>${escape(formState.duc)}</span></div>`);
    if (formState.comment) optionalLines.push(`<div><strong>Comment</strong><span>${escape(formState.comment)}</span></div>`);

    const emailTarget = recipientEmail
      ? `<strong>${escape(recipientEmail)}</strong>`
      : 'your account email';

    modal.innerHTML = `
      <h3 style="color:#15803d;">✓ Data request submitted</h3>
      <p class="sub">
        Request <strong>#${escape(String(reqId != null ? reqId : '—'))}</strong> for <strong>${escape(formState.project)}</strong>
        was created and is awaiting approval.
      </p>
      <div class="result-grid">
        <div><strong>Project</strong><span>${escape(formState.project)}</span></div>
        <div><strong>${escape(cohort.label.charAt(0).toUpperCase() + cohort.label.slice(1))} requested</strong><span>${cohort.count.toLocaleString()}</span></div>
        <div><strong>Status</strong><span>${escape(friendlyStatus)}</span></div>
        <div><strong>Request ID</strong><span>${escape(String(reqId != null ? reqId : '—'))}</span></div>
        ${optionalLines.join('')}
      </div>
      <p class="sub" style="margin-top:14px;">You'll receive an email at ${emailTarget}:</p>
      <ul class="next-steps">
        <li>once your request is <strong>approved</strong>, and</li>
        <li>again when the de-identified data is <strong>ready to download or use in XNAT</strong>.</li>
      </ul>
      ${requestUrl ? `<p class="sub"><a href="${escape(requestUrl)}" target="_blank" rel="noopener" style="color:#2563eb;">View request in XNAT &rarr;</a></p>` : ''}
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
    """Execute SQL against Scout's Trino + render rows as inline interactive iframe.

    Both methods take a SQL SELECT string, run it server-side via the Trino
    Python client, embed the results in a sandboxed iframe, and return a
    2-tuple `(HTMLResponse, summary_str)`. OWUI renders the HTMLResponse as
    an iframe at the tool-call indicator. Row data is JSON-encoded into the
    iframe HTML, so it never enters the LLM stream token-by-token — only the
    summary (row count, column names, sample rows, or SQL error) feeds back
    to the LLM.

    The earlier (rows-as-tool-args) design timed out on cohorts of ~100+ rows
    because the LLM had to re-emit ~35K output tokens just to pass the rows
    to the Tool. Moving SQL execution into the Tool keeps the LLM's
    contribution ≈200 tokens of SQL regardless of cohort size.

    On SQL error, the summary string surfaces the error + the failing query
    so the LLM can self-correct on the next turn — same recovery loop the
    LLM already follows for `scout-db_execute_query` errors.
    """

    class Valves(BaseModel):
        max_display_rows: int = Field(
            default=1000,
            description=(
                "Hard cap on rows fetched for the iframe table (full row "
                "data, embedded as JS). Browser performance degrades past "
                "a few thousand rows; the iframe paginates client-side at "
                "page_size rows per page, so large display caps are fine "
                "for sort/search but cost iframe HTML bytes."
            ),
        )
        max_accession_rows: int = Field(
            default=10000,
            description=(
                "Hard cap on the secondary `SELECT accession_number FROM "
                "(<llm sql>)` query. The iframe ships ALL of these to XNAT "
                "regardless of how many rows are visible in the table — "
                "researchers can't review every row at large cohort sizes "
                "but they're submitting based on the SQL-defined cohort, "
                "not what they scrolled to. Single column, scales to tens "
                "of thousands of rows cheaply."
            ),
        )
        page_size: int = Field(
            default=25,
            description="Rows per page in the iframe table's client-side pagination.",
        )
        max_sample_rows: int = Field(
            default=5,
            description="Number of sample rows included in the LLM context summary",
        )
        trino_host: str = Field(
            default="trino",
            description=(
                "Trino coordinator hostname (cluster-internal). Defaults to "
                "'trino' which resolves to trino.<chatbot_namespace> when "
                "OWUI runs in the same namespace."
            ),
        )
        trino_port: int = Field(
            default=8080,
            description="Trino coordinator port",
        )
        trino_catalog: str = Field(
            default="delta",
            description="Default catalog for queries (overridable in the SQL)",
        )
        trino_schema: str = Field(
            default="default",
            description="Default schema for queries (overridable in the SQL)",
        )
        trino_user: str = Field(
            default="trino",
            description=(
                "Trino user. Trino's built-in auth is permissive in our "
                "deploy; rows-level access is gated by the Trino MCP server's "
                "TRINO_ALLOWED_CATALOGS / TRINO_ALLOWED_SCHEMAS, which this "
                "tool does not bypass (we hit the same Trino coordinator "
                "with the same role)."
            ),
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
        xnat_external_url: str = Field(
            default="",
            description=(
                "External XNAT base URL shown to the user in the submission "
                "result page as a clickable 'View request in XNAT' link "
                "(built as {xnat_external_url}/xapi/iq/data-requests/{id}). "
                "Cosmetic — no fetch is made to this URL. Leave empty to "
                "hide the link entirely. Important: when set, the URL's "
                "domain must also be in the Link Sanitizer Filter's "
                "internal_domains valve (Workspace > Functions > Link "
                "Sanitizer) — otherwise the filter will scrub the URL out "
                "of the iframe HTML and break script execution."
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
    # SQL execution helpers
    # ------------------------------------------------------------------
    def _execute_sql(self, sql: str, max_rows: int) -> tuple[list[str], list[dict]]:
        """Run a SELECT against Trino and return (columns, rows).

        Caps row fetch at `max_rows`. Trino values that aren't natively
        JSON-serializable (datetime, Decimal, etc.) get coerced to strings
        via `default=str` at JSON-encode time, so the iframe sees ISO
        timestamps and numeric strings instead of opaque objects.

        Raises whatever Trino raises on failure — caller catches and returns
        the error string in the summary so the LLM can self-correct.
        """
        # Local import keeps the module loadable when `trino` isn't installed
        # (e.g., during pre-commit / lint where the requirements frontmatter
        # hasn't run). OWUI installs the requirement at tool-load time.
        import trino.dbapi

        conn = trino.dbapi.connect(
            host=self.valves.trino_host,
            port=int(self.valves.trino_port),
            user=self.valves.trino_user,
            catalog=self.valves.trino_catalog,
            schema=self.valves.trino_schema,
        )
        try:
            cur = conn.cursor()
            cur.execute(sql)
            columns = [desc[0] for desc in cur.description] if cur.description else []
            rows: list[dict] = []
            for row in cur:
                rows.append(dict(zip(columns, row)))
                if len(rows) >= max_rows:
                    break
            return columns, rows
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _execute_accessions_query(
        self, sql: str, max_accessions: int
    ) -> Optional[list[str]]:
        """Run `SELECT DISTINCT accession_number FROM (<sql>)` to get the full
        cohort's accession_numbers regardless of how many rows the display
        query fetched. Returns None on failure (caller falls back to using
        accession_numbers from the displayed rows only).

        Wrapping caveats:
          - The LLM's SQL must be a plain SELECT (no top-level CTE / WITH).
            Trino can't wrap a CTE in a subquery without rewriting; we
            don't try. If the LLM uses CTEs, this returns None and we fall
            back to displayed-row accessions only.
          - The LLM's SQL must include `accession_number` in its SELECT,
            otherwise the wrapper errors with "column not found". The
            system prompt's Cohort Building section enforces this — if it
            fails, the user sees an empty XNAT cohort, which is a clear
            signal the LLM forgot the column.
        """
        # Quick CTE check — peeking at the first non-comment, non-whitespace
        # token. If it's `WITH`, give up (Trino disallows wrap).
        stripped = sql.lstrip()
        # Strip leading -- and /* ... */ comments minimally
        while stripped.startswith("--") or stripped.startswith("/*"):
            if stripped.startswith("--"):
                eol = stripped.find("\n")
                stripped = stripped[eol + 1 :].lstrip() if eol != -1 else ""
            else:
                end = stripped.find("*/")
                stripped = stripped[end + 2 :].lstrip() if end != -1 else ""
        if stripped[:5].upper() == "WITH ":
            return None

        wrapped = (
            f"SELECT DISTINCT accession_number FROM ({sql.rstrip(' ;')}) sub "
            f"WHERE accession_number IS NOT NULL"
        )
        try:
            _cols, rows = self._execute_sql(wrapped, max_accessions)
        except Exception:
            return None
        return [str(r["accession_number"]) for r in rows if r.get("accession_number")]

    # ------------------------------------------------------------------
    # render_table — the single rendering entry point
    # ------------------------------------------------------------------
    async def render_table(
        self,
        sql: str,
        columns: Optional[list[str]] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __user__: dict = {},
        __chat_id__: str = "",
    ) -> tuple:
        """Execute SQL and render the rows as an inline interactive table.

        Pass a SQL SELECT statement — the Tool runs it server-side via
        Trino and embeds the rows in a sandboxed iframe with:

          - Sortable, filterable columns (click headers to sort, search box
            to filter, prev/next pagination)
          - Click any row to open a focused detail modal showing the full
            Findings, Impression, and metadata for that report — with
            arrow-key / button nav to step through the cohort one report
            at a time (the previous render_report_flipbook is now folded
            into this detail modal; there is no separate flipbook tool)
          - Send-to-XNAT button that ships the cohort's full
            accession_number list to XNAT (NOT just rows visible on screen)

        Rows do NOT pass through your tool-call arguments, so this scales
        to thousands of rows without timing out.

        Use whenever the user wants to SEE a cohort — "show me", "list",
        "find all", "browse", "let me read through". For pure aggregates
        (counts, averages, "how many"), call ``scout-db_execute_query``
        instead and answer in narrative.

        Don't add LIMIT to your SQL. The Tool caps the display rows at its
        max_display_rows valve and the accession_number list at its
        max_accession_rows valve. Adding LIMIT yourself would defeat the
        full-cohort accession list that goes to XNAT.

        SELECT the canonical column names so the detail modal can locate
        Findings / Impression / metadata: ``report_section_findings``,
        ``report_section_impression``, ``message_dt``, ``patient_age``,
        ``modality``, ``service_name``, ``sex``, plus
        ``COALESCE(patient_mpi, epic_mrn) AS patient_id`` and
        ``accession_number`` (XNAT's join key — required for
        Send-to-XNAT to work on cohorts larger than max_display_rows).

        On SQL error, returns the error + failing query as a string so
        you can self-correct on the next turn.

        :param sql: A Trino SELECT against delta.default. No top-level
          LIMIT (Tool handles caps). No top-level CTE if you want
          Send-to-XNAT to ship all accessions — the Tool wraps your
          SQL in a subquery to fetch the full accession list, and Trino
          can't wrap a CTE.
        :param columns: optional ordered list of column names to display.
          Defaults to the SELECT order returned by Trino.
        :return: (HTMLResponse, summary_string) — OWUI renders the
          iframe and only feeds the summary (row count, column names,
          sample rows) back to you. Do NOT re-display the row data as a
          markdown table.
        """
        await self._emit_status(__event_emitter__, "Querying Trino…", done=False)

        try:
            cols, rows = self._execute_sql(sql, self.valves.max_display_rows)
        except Exception as exc:
            err = (
                f"render_table SQL error: {exc}\n\n"
                f"Failing query:\n{sql}\n\n"
                f"Read the error, fix the query, and call render_table again."
            )
            await self._emit_status(__event_emitter__, "SQL error", done=True)
            return (err, err)

        if not rows:
            msg = (
                f"render_table: 0 rows returned by:\n{sql}\n\n"
                f"Tell the user no rows matched and offer to broaden the criteria."
            )
            await self._emit_status(__event_emitter__, "No rows", done=True)
            return (msg, msg)

        display_cols = columns or cols

        await self._emit_status(
            __event_emitter__, "Fetching cohort accessions…", done=False
        )
        all_accessions = self._execute_accessions_query(
            sql, self.valves.max_accession_rows
        )
        # Fall back to displayed-row accessions if the wrap query failed
        # (LLM used CTE, accession_number not in SELECT, or unrelated SQL
        # issue we don't want to escalate to a hard failure since the
        # display already worked).
        if all_accessions is None:
            all_accessions = [
                str(r["accession_number"]) for r in rows if r.get("accession_number")
            ]
            accession_note = (
                "could not run accessions wrapper (likely a CTE in your "
                "SQL or accession_number missing from the SELECT) — "
                f"Send-to-XNAT will ship only the {len(all_accessions)} "
                "accessions from the displayed rows. Rewrite without CTE "
                "and ensure accession_number is selected so the full "
                "cohort can ship."
            )
        elif len(all_accessions) >= self.valves.max_accession_rows:
            accession_note = (
                f"accession list capped at "
                f"max_accession_rows={self.valves.max_accession_rows}"
            )
        else:
            accession_note = ""

        await self._emit_status(__event_emitter__, "Rendering table…", done=False)

        html_doc = _build_table_doc(
            rows,
            display_cols,
            all_accessions,
            self._user_info(__user__),
            self.valves.bridge_url,
            self.valves.xnat_external_url,
            self.valves.page_size,
        )

        display_capped = len(rows) >= self.valves.max_display_rows
        display_note = (
            (
                f"display rows capped at "
                f"max_display_rows={self.valves.max_display_rows}; "
                f"cohort may be larger — accession list shipped to "
                f"XNAT is {len(all_accessions):,} regardless"
            )
            if display_capped
            else ""
        )
        notes = " | ".join(n for n in [display_note, accession_note] if n)
        notes_block = f"\n\nNotes: {notes}" if notes else ""

        summary = (
            f"render_table: Embedded UI result is active and visible to "
            f"the user. Do NOT re-display the data as text — the "
            f"rendered table above this message IS the user's view of "
            f"the result.\n\n"
            f"Table summary: {len(rows):,} row(s) shown across "
            f"{len(display_cols)} columns: {', '.join(display_cols)}.\n"
            f"Cohort accessions for XNAT: "
            f"{len(all_accessions):,}.{notes_block}\n\n"
            f"Sample ({min(self.valves.max_sample_rows, len(rows))} of "
            f"{len(rows):,} rows) — for your reasoning, not for "
            f"re-display:\n"
            + _summary_table_text(rows, display_cols, self.valves.max_sample_rows)
        )

        await self._emit_status(__event_emitter__, "Table ready", done=True)

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


def _safe_json(value: Any) -> str:
    """JSON-encode a value for safe embedding in an inline <script> block.

    `json.dumps` does not escape `<`, so any string in `value` containing
    `</script>`, `<!--`, or `<script>` would prematurely end the surrounding
    <script> tag when an HTML parser scans the document, leaving the rest of
    the script as plain text and breaking iframe rendering. Encoding `<` as
    `\\u003c` is read identically by JS (still a `<` character in the string
    value) but the HTML parser never sees a tag-like sequence.

    Standard mitigation; same as Django's escapejs filter and Flask's tojson.
    """
    return json.dumps(value, default=str).replace("<", "\\u003c")


def _build_table_doc(
    rows: list[dict],
    columns: list[str],
    all_accessions: list[str],
    user: dict | None,
    bridge_url: str,
    xnat_external_url: str,
    page_size: int,
) -> str:
    return (
        _TABLE_TMPL.replace("__ROWS__", _safe_json(rows))
        .replace("__COLS__", _safe_json(columns))
        .replace("__ACCESSIONS__", _safe_json(all_accessions or []))
        .replace("__USER__", _safe_json(user or None))
        .replace("__BRIDGE_URL__", _safe_json(bridge_url or ""))
        .replace("__XNAT_EXTERNAL_URL__", _safe_json(xnat_external_url or ""))
        .replace("__PAGE_SIZE__", str(int(page_size)))
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
    --accent-soft: #eff6ff;
  }}
  body {{
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    font-size: 13px;
    line-height: 1.45;
    color: var(--fg);
    background: var(--bg);
    -webkit-font-smoothing: antialiased;
  }}
  /* ---------- Toolbar ---------- */
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
    padding: 6px 10px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 12px;
    background: var(--bg-soft);
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

  /* ---------- Table ---------- */
  .table-wrap {{ overflow: auto; max-height: {_IFRAME_MAX_HEIGHT - 110}px; }}
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
  tbody tr.row-clickable {{ cursor: pointer; }}
  tbody tr:nth-child(even) td {{ background: var(--bg-soft); }}
  tbody tr.row-clickable:hover td {{ background: var(--bg-hover); }}
  td .clamp {{
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    overflow: hidden;
  }}
  .empty {{ color: var(--fg-subtle); font-style: italic; }}

  /* ---------- Pagination footer ---------- */
  .pagination {{
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    border-top: 1px solid var(--border);
    background: var(--bg-soft);
    font-size: 12px;
    color: var(--fg-muted);
    position: sticky;
    bottom: 0;
  }}
  .pagination button {{
    padding: 4px 12px;
    border: 1px solid var(--border);
    background: var(--bg);
    border-radius: 6px;
    cursor: pointer;
    font-size: 12px;
    color: var(--fg);
  }}
  .pagination button:hover:not(:disabled) {{
    background: var(--bg-hover);
    border-color: var(--fg-subtle);
  }}
  .pagination button:disabled {{ opacity: 0.4; cursor: not-allowed; }}
  .pagination .page-info {{ color: var(--fg-muted); }}
  .pagination .spacer {{ flex: 1; }}
  .pagination .cohort-note {{
    color: var(--fg-muted);
    font-size: 11px;
  }}

  /* ---------- Detail modal ---------- */
  .detail-overlay {{
    display: none;
    position: fixed; inset: 0;
    background: rgba(15, 23, 42, 0.55);
    z-index: 9000;
    align-items: center; justify-content: center;
    padding: 24px;
  }}
  .detail-overlay.open {{ display: flex; }}
  .detail-card {{
    background: var(--bg);
    width: min(900px, 100%);
    max-height: 100%;
    display: flex; flex-direction: column;
    border-radius: 14px;
    box-shadow: 0 20px 50px rgba(15, 23, 42, 0.25);
    overflow: hidden;
  }}
  .detail-header {{
    padding: 16px 22px;
    background: var(--bg-soft);
    border-bottom: 1px solid var(--border);
    display: flex; align-items: flex-start;
    gap: 14px;
  }}
  .detail-header-info {{ flex: 1; min-width: 0; }}
  .detail-pos {{
    font-size: 11px; font-weight: 600;
    color: var(--fg-muted);
    text-transform: uppercase; letter-spacing: 0.06em;
    margin-bottom: 8px;
  }}
  .detail-badges {{
    display: flex; flex-wrap: wrap; gap: 6px;
  }}
  .detail-badge {{
    font-size: 12px; font-weight: 500;
    padding: 3px 10px; border-radius: 999px;
    background: var(--accent-soft); color: var(--accent);
    white-space: nowrap;
  }}
  .detail-badge.muted {{
    background: var(--bg-hover); color: #4b5563;
  }}
  .detail-close {{
    background: none; border: none;
    color: var(--fg-subtle); cursor: pointer;
    font-size: 22px; line-height: 1;
    padding: 2px 10px; border-radius: 6px;
    flex-shrink: 0;
  }}
  .detail-close:hover {{ color: var(--fg); background: var(--bg-hover); }}
  .detail-body {{
    flex: 1; overflow-y: auto;
    padding: 20px 22px;
  }}
  .detail-section {{ margin-bottom: 22px; }}
  .detail-section:last-child {{ margin-bottom: 0; }}
  .detail-section h4 {{
    font-size: 11px; font-weight: 700;
    color: var(--fg-muted);
    text-transform: uppercase; letter-spacing: 0.06em;
    margin: 0 0 8px;
  }}
  .detail-prose {{
    font-size: 14px; line-height: 1.65;
    color: var(--fg);
    white-space: pre-wrap;
    background: var(--bg-soft);
    padding: 14px 16px; border-radius: 8px;
    border-left: 3px solid var(--border);
  }}
  .detail-prose.impression {{
    border-left-color: var(--accent);
    background: var(--accent-soft);
  }}
  .detail-meta-grid {{
    display: grid; grid-template-columns: max-content 1fr;
    gap: 6px 18px;
    font-size: 13px;
  }}
  .detail-meta-grid dt {{ color: var(--fg-muted); font-weight: 500; }}
  .detail-meta-grid dd {{ margin: 0; color: var(--fg); word-break: break-word; }}
  .detail-footer {{
    padding: 12px 22px;
    background: var(--bg-soft);
    border-top: 1px solid var(--border);
    display: flex; align-items: center; gap: 10px;
  }}
  .detail-footer .spacer {{ flex: 1; }}
  .detail-footer button {{
    padding: 7px 14px; border-radius: 7px;
    border: 1px solid var(--border); background: var(--bg);
    color: var(--fg); cursor: pointer;
    font-size: 13px; font-weight: 500;
  }}
  .detail-footer button:hover:not(:disabled) {{
    background: var(--bg-hover); border-color: var(--fg-subtle);
  }}
  .detail-footer button:disabled {{ opacity: 0.4; cursor: not-allowed; }}
  .detail-footer-hint {{ font-size: 11px; color: var(--fg-subtle); }}
</style>
</head>
<body>
<div class="toolbar">
  <input id="q" type="search" placeholder="Filter rows…" />
  <span class="info" id="row-summary"></span>
  <span class="spacer"></span>
  {_XNAT_BUTTON_HTML}
</div>
<div class="table-wrap">
  <table id="t"><thead><tr id="head"></tr></thead><tbody id="body"></tbody></table>
</div>
<div class="pagination">
  <button id="page-prev" type="button" aria-label="Previous page">←</button>
  <span class="page-info">Page <span id="page-idx">1</span> of <span id="page-total">1</span></span>
  <button id="page-next" type="button" aria-label="Next page">→</button>
  <span class="spacer"></span>
  <span class="cohort-note" id="cohort-note"></span>
</div>

<div id="detail-overlay" class="detail-overlay" role="dialog" aria-modal="true" aria-hidden="true">
  <div class="detail-card">
    <header class="detail-header">
      <div class="detail-header-info">
        <div class="detail-pos">Report <span id="detail-pos-idx">1</span> of <span id="detail-pos-total">0</span></div>
        <div class="detail-badges" id="detail-badges"></div>
      </div>
      <button class="detail-close" id="detail-close" type="button" aria-label="Close">&times;</button>
    </header>
    <div class="detail-body">
      <section class="detail-section">
        <h4>Impression</h4>
        <div class="detail-prose impression" id="detail-impression"></div>
      </section>
      <section class="detail-section">
        <h4>Findings</h4>
        <div class="detail-prose" id="detail-findings"></div>
      </section>
      <section class="detail-section detail-other-section">
        <h4>Other fields</h4>
        <dl class="detail-meta-grid" id="detail-other"></dl>
      </section>
    </div>
    <footer class="detail-footer">
      <button id="detail-prev" type="button">&larr; Previous</button>
      <button id="detail-next" type="button">Next &rarr;</button>
      <span class="spacer"></span>
      <span class="detail-footer-hint">&larr;/&rarr; keys to navigate · Esc to close</span>
    </footer>
  </div>
</div>

<script>
const ROWS = __ROWS__;
const COLS = __COLS__;
const ALL_ACCESSIONS = __ACCESSIONS__;
const USER = __USER__;
const BRIDGE_URL = __BRIDGE_URL__;
const XNAT_EXTERNAL_URL = __XNAT_EXTERNAL_URL__;
const PAGE_SIZE = __PAGE_SIZE__;
const CLAMP_THRESHOLD = 140;

// Field-name fallback chains for the detail modal — the LLM sometimes
// aliases columns in SQL (SELECT report_section_impression AS impression).
const FINDINGS_KEYS = ['report_section_findings', 'findings', 'report_findings'];
const IMPRESSION_KEYS = ['report_section_impression', 'impression', 'report_impression'];
const MRN_KEYS = ['patient_id', 'epic_mrn', 'mrn', 'patient_mpi'];
const DATE_KEYS = ['message_dt', 'timestamp', 'date', 'report_date'];
const HIDE_FROM_OTHER = new Set([
  ...FINDINGS_KEYS, ...IMPRESSION_KEYS, ...MRN_KEYS, ...DATE_KEYS,
  'modality', 'service_name', 'service', 'patient_age', 'age',
  'sex', 'gender', 'accession_number', 'study_instance_uid', 'report_text',
]);

let sortCol = null, sortDir = 1, filter = "";
let pageIdx = 0;
let currentRows = ROWS;  // post-filter, post-sort

function fmt(v) {{
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}}
function compare(a, b) {{
  if (a == null) return 1; if (b == null) return -1;
  const na = Number(a), nb = Number(b);
  if (!isNaN(na) && !isNaN(nb)) return (na - nb) * sortDir;
  return String(a).localeCompare(String(b)) * sortDir;
}}
function pickField(record, keys) {{
  for (const k of keys) {{
    if (record[k] != null && record[k] !== '') return record[k];
  }}
  return null;
}}
function formatDate(v) {{
  if (!v) return '';
  const m = String(v).match(/^(\\d{{4}}-\\d{{2}}-\\d{{2}})/);
  return m ? m[1] : String(v);
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
      pageIdx = 0;
      refresh();
    }});
    tr.appendChild(th);
  }});
}}

function refresh() {{
  // Recompute filter+sort from ROWS
  let rows = ROWS;
  if (filter) {{
    const f = filter.toLowerCase();
    rows = rows.filter(r => COLS.some(c => fmt(r[c]).toLowerCase().includes(f)));
  }}
  if (sortCol) rows = [...rows].sort((a, b) => compare(a[sortCol], b[sortCol]));
  currentRows = rows;
  renderTable();
}}

function renderTable() {{
  const totalRows = currentRows.length;
  const pageCount = Math.max(1, Math.ceil(totalRows / PAGE_SIZE));
  if (pageIdx >= pageCount) pageIdx = pageCount - 1;
  if (pageIdx < 0) pageIdx = 0;
  const startRow = pageIdx * PAGE_SIZE;
  const visibleRows = currentRows.slice(startRow, startRow + PAGE_SIZE);

  renderHead();
  const body = document.getElementById("body");
  body.innerHTML = "";
  visibleRows.forEach((r) => {{
    const tr = document.createElement("tr");
    tr.classList.add("row-clickable");
    // Find this row's index in currentRows so the detail modal navigates
    // through the same sort+filter view the user is looking at.
    const rowIdx = startRow + visibleRows.indexOf(r);
    tr.addEventListener("click", () => openDetail(currentRows, rowIdx));
    COLS.forEach((c) => {{
      const td = document.createElement("td");
      const v = fmt(r[c]);
      if (v === "") {{
        td.innerHTML = '<span class="empty">—</span>';
      }} else if (v.length > CLAMP_THRESHOLD) {{
        const clamp = document.createElement("div");
        clamp.className = "clamp";
        clamp.textContent = v;
        td.appendChild(clamp);
      }} else {{
        td.textContent = v;
      }}
      tr.appendChild(td);
    }});
    body.appendChild(tr);
  }});

  // Toolbar info line
  const summary = (filter || sortCol)
    ? `${{visibleRows.length.toLocaleString()}} shown · ${{totalRows.toLocaleString()}} matching · ${{ROWS.length.toLocaleString()}} total`
    : `${{ROWS.length.toLocaleString()}} report${{ROWS.length === 1 ? '' : 's'}}`;
  document.getElementById("row-summary").textContent = summary;

  // Pagination
  document.getElementById("page-idx").textContent = pageIdx + 1;
  document.getElementById("page-total").textContent = pageCount;
  document.getElementById("page-prev").disabled = pageIdx === 0;
  document.getElementById("page-next").disabled = pageIdx >= pageCount - 1;

  // Cohort note — what ships to XNAT
  const accCount = (typeof ALL_ACCESSIONS !== 'undefined' && Array.isArray(ALL_ACCESSIONS))
    ? ALL_ACCESSIONS.length : 0;
  document.getElementById("cohort-note").textContent =
    `${{accCount.toLocaleString()}} accession${{accCount === 1 ? '' : 's'}} ship to XNAT`;

  reportHeight();
}}

document.getElementById("q").addEventListener("input", (e) => {{
  filter = e.target.value;
  pageIdx = 0;
  refresh();
}});
document.getElementById("page-prev").addEventListener("click", () => {{
  if (pageIdx > 0) {{ pageIdx--; renderTable(); document.querySelector('.table-wrap').scrollTop = 0; }}
}});
document.getElementById("page-next").addEventListener("click", () => {{
  const pageCount = Math.max(1, Math.ceil(currentRows.length / PAGE_SIZE));
  if (pageIdx < pageCount - 1) {{ pageIdx++; renderTable(); document.querySelector('.table-wrap').scrollTop = 0; }}
}});

// ---------- Detail modal ----------
const detailOverlay = document.getElementById('detail-overlay');
let detailIdx = 0;
let detailList = [];

function renderBadges(r) {{
  const el = document.getElementById('detail-badges');
  el.innerHTML = '';
  const items = [];
  const mrn = pickField(r, MRN_KEYS);
  if (mrn) items.push({{label: mrn, primary: true}});
  if (r.modality) items.push({{label: r.modality}});
  const svc = r.service_name || r.service;
  if (svc) items.push({{label: svc}});
  const date = pickField(r, DATE_KEYS);
  if (date) items.push({{label: formatDate(date)}});
  if (r.patient_age != null) items.push({{label: r.patient_age + 'y'}});
  if (r.sex || r.gender) items.push({{label: r.sex || r.gender}});
  items.forEach((b) => {{
    const span = document.createElement('span');
    span.className = b.primary ? 'detail-badge' : 'detail-badge muted';
    span.textContent = b.label;
    el.appendChild(span);
  }});
}}

function renderDetail() {{
  const r = detailList[detailIdx] || {{}};
  document.getElementById('detail-pos-idx').textContent = detailIdx + 1;
  document.getElementById('detail-pos-total').textContent = detailList.length;
  renderBadges(r);
  document.getElementById('detail-impression').textContent =
    pickField(r, IMPRESSION_KEYS) || '(no impression recorded)';
  document.getElementById('detail-findings').textContent =
    pickField(r, FINDINGS_KEYS) || '(no findings recorded)';
  const otherDl = document.getElementById('detail-other');
  otherDl.innerHTML = '';
  const otherKeys = Object.keys(r).filter((k) => !HIDE_FROM_OTHER.has(k));
  const otherSec = document.querySelector('.detail-other-section');
  if (otherKeys.length === 0) {{
    otherSec.style.display = 'none';
  }} else {{
    otherSec.style.display = '';
    otherKeys.forEach((k) => {{
      const dt = document.createElement('dt'); dt.textContent = k;
      const dd = document.createElement('dd'); dd.textContent = fmt(r[k]);
      otherDl.appendChild(dt); otherDl.appendChild(dd);
    }});
  }}
  document.getElementById('detail-prev').disabled = detailIdx === 0;
  document.getElementById('detail-next').disabled = detailIdx >= detailList.length - 1;
}}

function openDetail(rows, startIdx) {{
  detailList = rows || [];
  detailIdx = Math.max(0, Math.min(startIdx || 0, detailList.length - 1));
  renderDetail();
  detailOverlay.classList.add('open');
  detailOverlay.setAttribute('aria-hidden', 'false');
  parent.postMessage({{ type: 'iframe:height', height: {_IFRAME_MAX_HEIGHT} }}, '*');
}}
function closeDetail() {{
  detailOverlay.classList.remove('open');
  detailOverlay.setAttribute('aria-hidden', 'true');
  if (typeof reportHeight === 'function') reportHeight(true);
}}

document.getElementById('detail-prev').onclick = () => {{
  if (detailIdx > 0) {{ detailIdx--; renderDetail(); }}
}};
document.getElementById('detail-next').onclick = () => {{
  if (detailIdx < detailList.length - 1) {{ detailIdx++; renderDetail(); }}
}};
document.getElementById('detail-close').onclick = closeDetail;
detailOverlay.addEventListener('click', (e) => {{
  if (e.target === detailOverlay) closeDetail();
}});
document.addEventListener('keydown', (e) => {{
  if (!detailOverlay.classList.contains('open')) return;
  if (e.key === 'Escape') closeDetail();
  if (e.key === 'ArrowLeft' && detailIdx > 0) {{ detailIdx--; renderDetail(); }}
  if (e.key === 'ArrowRight' && detailIdx < detailList.length - 1) {{ detailIdx++; renderDetail(); }}
}});

// ---------- Height reporting ----------
let _lastReportedHeight = -1;
function reportHeight(force) {{
  const h = Math.min(document.documentElement.scrollHeight, {_IFRAME_MAX_HEIGHT});
  if (!force && h === _lastReportedHeight) return;
  _lastReportedHeight = h;
  parent.postMessage({{ type: "iframe:height", height: h }}, "*");
}}
[0, 50, 200, 600].forEach((d) => setTimeout(() => reportHeight(true), d));
new MutationObserver(() => reportHeight()).observe(document.body, {{ childList: true, subtree: true, characterData: true }});
window.addEventListener("resize", () => reportHeight());
if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => reportHeight(true));

refresh();
</script>
{_XNAT_OVERLAY_HTML}
<script>{_XNAT_MODAL_JS}</script>
</body>
</html>
"""
