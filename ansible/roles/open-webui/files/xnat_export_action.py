"""
title: Send to XNAT
description: Export Scout query results to an XNAT instance. Adds a button to
             the message toolbar.
author: Scout Team
version: 0.1.0
"""

import csv
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

# Greyscale "external link" icon — placeholder until a real XNAT logo is ready.
# Source SVG lives alongside this file at xnat_logo_grey.svg.
_ICON_DATA_URI = (
    "data:image/svg+xml;base64,"
    "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyMCIg"
    "aGVpZ2h0PSIyMCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9"
    "IiM4ODgiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJv"
    "a2UtbGluZWpvaW49InJvdW5kIj4KICA8cGF0aCBkPSJNMjEgM0wxNCAzTDE0IDEwIi8+"
    "CiAgPHBhdGggZD0iTTIxIDNMMTAgMTQiLz4KICA8cGF0aCBkPSJNMTcgMTN2NmEyIDIg"
    "MCAwIDEtMiAySDVhMiAyIDAgMCAxLTItMlY5YTIgMiAwIDAgMSAyLTJoNiIvPgo8L3N2"
    "Zz4K"
)

# ---------------------------------------------------------------------------
# CSS variables used by Open WebUI that give us light/dark mode support.
# Fallback values ensure the dialogs look reasonable even outside Open WebUI.
# ---------------------------------------------------------------------------
_MODAL_STYLES = """
    .xnat-overlay {
        position: fixed; inset: 0;
        background: rgba(0,0,0,0.5);
        display: flex; align-items: center; justify-content: center;
        z-index: 9999;
    }
    .xnat-modal {
        background: var(--color-surface-primary, #1e1e1e);
        color: var(--color-text-primary, #e0e0e0);
        padding: 24px; border-radius: 12px;
        min-width: 400px; max-width: 600px;
        max-height: 80vh; overflow: auto;
        box-shadow: 0 8px 32px rgba(0,0,0,0.3);
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 14px;
    }
    .xnat-modal h3 {
        margin: 0 0 16px; font-size: 18px;
    }
    .xnat-modal label {
        display: block; margin-bottom: 4px;
        font-weight: 600; font-size: 13px;
    }
    .xnat-modal select, .xnat-modal input[type=text] {
        width: 100%; padding: 8px; margin-bottom: 12px;
        border-radius: 6px; border: 1px solid #555;
        background: var(--color-surface-secondary, #2a2a2a);
        color: var(--color-text-primary, #e0e0e0);
        font-size: 14px;
    }
    .xnat-modal .info-block {
        background: var(--color-surface-secondary, #2a2a2a);
        border-radius: 6px; padding: 12px; margin-bottom: 12px;
        font-size: 13px; line-height: 1.5;
    }
    .xnat-modal pre {
        background: var(--color-surface-secondary, #2a2a2a);
        border-radius: 6px; padding: 12px; margin-bottom: 12px;
        font-size: 12px; overflow: auto; max-height: 200px;
        white-space: pre-wrap; word-break: break-all;
    }
    .xnat-modal .btn-row {
        display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px;
    }
    .xnat-modal button {
        padding: 8px 16px; border-radius: 6px; cursor: pointer;
        font-size: 14px; border: 1px solid #555;
        background: var(--color-surface-secondary, #3a3a3a);
        color: var(--color-text-primary, #e0e0e0);
    }
    .xnat-modal button:hover { opacity: 0.85; }
    .xnat-modal button.primary {
        background: #2563eb; color: #fff; border-color: #2563eb;
    }
"""


class Action:
    """Open WebUI Action that exports Scout query results to XNAT."""

    class Valves(BaseModel):
        xnat_base_url: str = Field(
            default="http://mock-xnat.scout-analytics.svc.cluster.local:8000",
            description="XNAT API base URL (internal, used for server-side requests). For POC, points to the mock XNAT service.",
        )
        xnat_external_url: str = Field(
            default="https://xnat.example.org",
            description="XNAT external URL (browser-facing, shown to users in links). Set to the real XNAT web UI base URL.",
        )
        xnat_service_account_id: str = Field(
            default="",
            description="XNAT service account ID for API access",
        )
        xnat_service_account_secret: str = Field(
            default="",
            description="XNAT service account secret",
        )
        send_timeout_seconds: int = Field(
            default=30,
            description="HTTP timeout in seconds for the XNAT API call",
        )
        debug_show_body: bool = Field(
            default=False,
            description=(
                "Show a dialog with the raw action body before proceeding. "
                "Useful for inspecting the message structure."
            ),
        )
        priority: int = Field(
            default=0,
            description="Button order priority (lower = leftmost)",
        )

    actions = [
        {
            "id": "send_to_xnat",
            "name": "Send to XNAT",
            "icon_url": _ICON_DATA_URI,
        },
    ]

    def __init__(self) -> None:
        self.valves = self.Valves()

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    async def action(
        self,
        body: dict,
        __user__: dict = {},
        __event_emitter__: Callable[..., Awaitable[Any]] | None = None,
        __event_call__: Callable[..., Awaitable[Any]] | None = None,
        __metadata__: dict = {},
        __request__: Any = None,
        __id__: str = "",
        __oauth_token__: dict = {},
    ) -> dict | None:
        """Called when the user clicks the Send to XNAT button."""
        if __event_call__ is None:
            return None

        log.info("action() called, %d messages", len(body.get("messages", [])))

        # -- Debug: show raw body in a dialog if valve is on ---------------
        if self.valves.debug_show_body:
            body_summary = self._summarize_body_for_debug(body)
            await __event_call__(
                {
                    "type": "execute",
                    "data": {"code": self._build_debug_dialog_js(body_summary)},
                }
            )

        # -- Step 1: Find CSV files for this user ----------------------------
        user_id = __user__.get("id", "")
        files = self._collect_csv_files(body, user_id)
        log.info("CSV files found for user %s: %s", user_id, files)
        if not files:
            await self._notify(
                __event_emitter__, "No query results to export", error=True
            )
            return None

        # -- Step 2: Extract user identity ---------------------------------
        user_info = self._extract_user_info(__user__, __oauth_token__)

        # -- Step 3 / 4 loop: input form and confirmation ------------------
        while True:
            # Show the input form
            input_result = await __event_call__(
                {
                    "type": "execute",
                    "data": {"code": self._build_input_form_js(files)},
                }
            )
            log.info(
                "Input form result: %s (type=%s)",
                input_result,
                type(input_result).__name__,
            )
            if not input_result:
                return None  # cancelled

            # __event_call__ returns strings, not dicts — parse the JSON
            if isinstance(input_result, str):
                input_result = json.loads(input_result)

            file_id: str = input_result.get("file_id", "")
            project_name: str = input_result.get("project_name", "").strip()
            if not project_name:
                await self._notify(
                    __event_emitter__, "Project name is required", error=True
                )
                continue

            selected_file = next((f for f in files if f["id"] == file_id), files[0])

            # Read file and extract accession numbers for confirmation
            accession_numbers: list[str] = []
            row_count = 0
            try:
                file_content = self._read_file_content(file_id)
                reader = csv.DictReader(io.StringIO(file_content))
                acc_col = None
                for col in reader.fieldnames or []:
                    if col.lower() == "accession_number":
                        acc_col = col
                        break
                if acc_col:
                    for row in reader:
                        row_count += 1
                        val = row.get(acc_col, "").strip()
                        if val:
                            accession_numbers.append(val)
                else:
                    row_count = sum(1 for _ in reader)
            except Exception as exc:
                log.warning("Failed to read file %s: %s", file_id, exc)

            if not accession_numbers and row_count > 0:
                await self._notify(
                    __event_emitter__,
                    "No accession_number column found in the selected file",
                    error=True,
                )
                continue

            # Show the confirmation dialog
            confirm_result = await __event_call__(
                {
                    "type": "execute",
                    "data": {
                        "code": self._build_confirmation_js(
                            file_name=selected_file["name"],
                            user_info=user_info,
                            project_name=project_name,
                            accession_numbers=accession_numbers,
                            row_count=row_count,
                        )
                    },
                }
            )

            if not confirm_result:
                return None  # cancelled

            if isinstance(confirm_result, str):
                confirm_result = json.loads(confirm_result)

            action = confirm_result.get("action", "cancel")
            if action == "back":
                continue  # loop back to input form
            if action == "send":
                break  # proceed to send
            return None  # cancel or unknown

        # -- Step 5: Send to XNAT ------------------------------------------
        xnat_url = f"{self.valves.xnat_base_url.rstrip('/')}/export"
        payload: dict[str, Any] = {
            "project_id": project_name,
            "requested_by": {
                "name": user_info.get("name", "Unknown"),
                "email": user_info.get("email", ""),
                "username": user_info.get("username", ""),
            },
            "accession_numbers": accession_numbers,
            "total_count": len(accession_numbers),
            "source": "scout-open-webui",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        response_data: dict[str, Any] | None = None
        error_message: str | None = None
        status_code: int | None = None

        try:
            async with httpx.AsyncClient(
                timeout=self.valves.send_timeout_seconds
            ) as client:
                resp = await client.post(
                    xnat_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                status_code = resp.status_code
                response_data = resp.json()
                if resp.status_code >= 400:
                    error_message = f"XNAT returned HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            error_message = f"Request failed: {exc}"
        except Exception as exc:
            error_message = f"Unexpected error: {exc}"

        # -- Step 6: Show result -------------------------------------------
        await __event_call__(
            {
                "type": "execute",
                "data": {
                    "code": self._build_result_js(
                        payload=payload,
                        response_data=response_data,
                        error_message=error_message,
                        status_code=status_code,
                        xnat_external_url=self.valves.xnat_external_url,
                        project_name=project_name,
                    )
                },
            }
        )
        return None

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _collect_csv_files(body: dict, user_id: str) -> list[dict[str, str]]:
        """Find CSV files created by the Scout Query Tool.

        The tool stores results via Files.insert_new_file() and emits a
        ``files`` event, but neither mechanism populates the action body's
        ``messages`` with file references.  Instead, we query the Files
        model directly for this user's CSV files.

        TODO: Improve this by linking files to chats via
        Chats.insert_chat_files() in the query tool, then looking up
        files by chat_id here with
        Chats.get_chat_files_by_chat_id_and_message_id().
        """
        from open_webui.models.files import Files

        all_files = Files.get_files_by_user_id(user_id)
        csv_files = sorted(
            [
                {"id": f.id, "name": f.filename, "created_at": f.created_at or 0}
                for f in all_files
                if f.filename and f.filename.lower().endswith(".csv")
            ],
            key=lambda f: f["created_at"],
            reverse=True,
        )
        # Strip the sort key before returning
        return [{"id": f["id"], "name": f["name"]} for f in csv_files]

    @staticmethod
    def _extract_user_info(user: dict, oauth_token: dict) -> dict[str, str]:
        """Pull user identity from the OAuth token or Open WebUI user dict."""
        info: dict[str, str] = {
            "name": user.get("name", "Unknown"),
            "email": user.get("email", ""),
        }

        # Try to decode the id_token JWT payload (no verification needed)
        id_token = oauth_token.get("id_token", "")
        if id_token:
            try:
                import base64

                # JWT is header.payload.signature — grab payload
                parts = id_token.split(".")
                if len(parts) >= 2:
                    padded = parts[1] + "=" * (-len(parts[1]) % 4)
                    claims = json.loads(base64.b64decode(padded))
                    info["name"] = claims.get("name", info["name"])
                    info["email"] = claims.get("email", info["email"])
                    info["username"] = claims.get(
                        "preferred_username", info.get("username", "")
                    )
            except Exception:
                pass  # fall back to __user__ values

        return info

    @staticmethod
    def _read_file_content(file_id: str) -> str:
        """Read a file's content from Open WebUI storage by file ID."""
        from open_webui.models.files import Files
        from open_webui.storage.provider import Storage

        file_record = Files.get_file_by_id(file_id)
        if not file_record:
            raise FileNotFoundError(f"File {file_id} not found")
        # Storage.get_file() returns the local filesystem path
        local_path = Storage.get_file(file_record.path)
        with open(local_path, "r", encoding="utf-8") as f:
            return f.read()

    # ------------------------------------------------------------------
    # Debug helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _summarize_body_for_debug(body: dict) -> str:
        """Build a readable summary of the body structure for debugging.

        Shows message roles, content lengths, and — critically — all keys
        present on each message so we can see where files actually live.
        """
        lines: list[str] = []
        lines.append(f"Top-level keys: {sorted(body.keys())}")
        lines.append("")

        for i, msg in enumerate(body.get("messages", [])):
            role = msg.get("role", "?")
            content = msg.get("content", "")
            msg_keys = sorted(msg.keys())
            lines.append(f"--- message[{i}] ---")
            lines.append(f"  role: {role}")
            lines.append(f"  keys: {msg_keys}")

            # Show full content for assistant messages (where tool output
            # and file references may be embedded), truncated for others.
            if role == "assistant":
                # Show first 2000 chars — enough to see tool output
                preview = content[:2000]
                if len(content) > 2000:
                    preview += f"\n... ({len(content) - 2000} more chars)"
                lines.append(f"  content ({len(content)} chars):")
                lines.append(preview)
            else:
                preview = content[:200] + "..." if len(content) > 200 else content
                lines.append(f"  content ({len(content)} chars): {preview}")

            # Show ALL extra keys beyond the standard set
            for key in sorted(msg.keys()):
                if key not in ("content", "role", "id", "timestamp"):
                    val = msg[key]
                    lines.append(f"  {key}: {json.dumps(val, default=str)[:500]}")

        return "\n".join(lines)

    @staticmethod
    def _build_debug_dialog_js(body_summary: str) -> str:
        """Return JS that shows the body summary in a scrollable dialog."""
        escaped = _js_escape(body_summary)
        return f"""
return new Promise((resolve) => {{
    const overlay = document.createElement('div');
    overlay.innerHTML = `
        <style>{_js_escape(_MODAL_STYLES)}</style>
        <div class="xnat-overlay">
            <div class="xnat-modal" style="min-width:600px;max-width:800px;">
                <h3>Debug: Action Body</h3>
                <pre style="max-height:60vh;font-size:11px;">{escaped}</pre>
                <div class="btn-row">
                    <button id="xnat-debug-ok" class="primary">Continue</button>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    document.getElementById('xnat-debug-ok').onclick = () => {{
        overlay.remove(); resolve(null);
    }};
}});
"""

    # ------------------------------------------------------------------
    # Dialog JS builders
    # ------------------------------------------------------------------
    def _build_input_form_js(self, files: list[dict[str, str]]) -> str:
        """Return JS that shows the input form and resolves with user input."""
        if len(files) == 1:
            file_field = (
                f"<label>Results file</label>"
                f'<input type="text" value="{_js_escape(files[0]["name"])}" '
                f"disabled />"
            )
            file_id_expr = f'"{_js_escape(files[0]["id"])}"'
        else:
            options = "".join(
                f'<option value="{_js_escape(f["id"])}">'
                f'{_js_escape(f["name"])}</option>'
                for f in files
            )
            file_field = (
                f"<label>Results file</label>"
                f'<select id="xnat-file">{options}</select>'
            )
            file_id_expr = 'document.getElementById("xnat-file").value'

        return f"""
return new Promise((resolve) => {{
  try {{
    const overlay = document.createElement('div');
    overlay.innerHTML = `
        <style>{_js_escape(_MODAL_STYLES)}</style>
        <div class="xnat-overlay">
            <div class="xnat-modal">
                <h3>Send to XNAT</h3>
                {file_field}
                <label>XNAT project name</label>
                <input type="text" id="xnat-project" placeholder="e.g. MyProject" />
                <div class="btn-row">
                    <button id="xnat-cancel">Cancel</button>
                    <button id="xnat-next" class="primary">Next</button>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    document.getElementById('xnat-cancel').onclick = () => {{
        overlay.remove(); resolve(null);
    }};
    document.getElementById('xnat-next').onclick = () => {{
      try {{
        const project = document.getElementById('xnat-project').value;
        const fileId = {file_id_expr};
        const result = JSON.stringify({{ file_id: fileId, project_name: project }});
        console.log('[XNAT] resolving with:', result);
        overlay.remove();
        resolve(result);
      }} catch(e) {{
        console.error('[XNAT] Next click error:', e);
        overlay.remove();
        resolve(null);
      }}
    }};

    const inp = document.getElementById('xnat-project');
    if (inp) inp.focus();
  }} catch(e) {{
    console.error('[XNAT] Dialog setup error:', e);
    resolve(null);
  }}
}});
"""

    @staticmethod
    def _build_confirmation_js(
        file_name: str,
        user_info: dict[str, str],
        project_name: str,
        accession_numbers: list[str] | None = None,
        row_count: int = 0,
    ) -> str:
        """Return JS that shows the confirmation dialog."""
        user_display = _js_escape(user_info.get("name", "Unknown"))
        email = _js_escape(user_info.get("email", ""))
        user_line = f"{user_display} &lt;{email}&gt;" if email else user_display

        accession_numbers = accession_numbers or []
        n_acc = len(accession_numbers)
        if n_acc > 0:
            # Show up to 20 in a two-column grid; truncate with a note if more
            show_limit = 20
            shown = accession_numbers[:show_limit]
            grid_items = "".join(
                f'<div style="padding:2px 0;">{_js_escape(a)}</div>' for a in shown
            )
            overflow = ""
            if n_acc > show_limit:
                overflow = (
                    f'<div style="grid-column:1/-1;color:#aaa;padding-top:4px;">'
                    f"… and {n_acc - show_limit} more</div>"
                )
            acc_section = (
                f'<div style="margin-top:8px;"><strong>Accession numbers ({n_acc}):</strong></div>'
                f'<div style="display:grid;grid-template-columns:1fr 1fr;'
                f"gap:0 16px;font-size:12px;font-family:monospace;"
                f"margin-top:4px;max-height:160px;overflow:auto;"
                f"padding:8px;background:var(--color-surface-tertiary, #222);"
                f'border-radius:4px;">'
                f"{grid_items}{overflow}</div>"
            )
        else:
            acc_section = (
                '<div style="color:#f59e0b;"><strong>Warning:</strong> '
                "No accession numbers found</div>"
            )

        return f"""
return new Promise((resolve) => {{
    const overlay = document.createElement('div');
    overlay.innerHTML = `
        <style>{_js_escape(_MODAL_STYLES)}</style>
        <div class="xnat-overlay">
            <div class="xnat-modal">
                <h3>Confirm Export</h3>
                <div class="info-block">
                    <div><strong>User:</strong> {user_line}</div>
                    <div><strong>File:</strong> {_js_escape(file_name)} ({row_count} rows)</div>
                    <div><strong>Destination:</strong> {_js_escape(project_name)}</div>
                    {acc_section}
                </div>
                <div class="btn-row">
                    <button id="xnat-cancel">Cancel</button>
                    <button id="xnat-back">Back</button>
                    <button id="xnat-send" class="primary">Send</button>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    document.getElementById('xnat-cancel').onclick = () => {{
        overlay.remove(); resolve(null);
    }};
    document.getElementById('xnat-back').onclick = () => {{
        overlay.remove(); resolve(JSON.stringify({{ action: 'back' }}));
    }};
    document.getElementById('xnat-send').onclick = () => {{
        overlay.remove(); resolve(JSON.stringify({{ action: 'send' }}));
    }};
}});
"""

    @staticmethod
    def _build_result_js(
        payload: dict[str, Any],
        response_data: dict[str, Any] | None,
        error_message: str | None,
        status_code: int | None,
        xnat_external_url: str,
        project_name: str,
    ) -> str:
        """Return JS that shows the result of the XNAT export."""
        payload_json = _js_escape(json.dumps(payload, indent=2))
        link = f"{xnat_external_url.rstrip('/')}/data/projects/{project_name}"

        if error_message:
            status_section = (
                f'<p style="color:#ef4444;font-weight:600;">'
                f"{_js_escape(error_message)}</p>"
            )
            if response_data is not None:
                response_json = _js_escape(json.dumps(response_data, indent=2))
                status_section += f"<pre>{response_json}</pre>"
            title = "XNAT Export \\u2014 Error"
        else:
            response_json = _js_escape(
                json.dumps(response_data, indent=2)
                if response_data is not None
                else "{}"
            )
            status_section = (
                f'<p style="color:#22c55e;font-weight:600;">Sent (HTTP {status_code})</p>'
                f"<label>Response</label>"
                f"<pre>{response_json}</pre>"
                f'<p><a href="{_js_escape(link)}" target="_blank" '
                f'style="color:#60a5fa;">{_js_escape(link)}</a></p>'
            )
            title = "XNAT Export \\u2014 Sent"

        return f"""
return new Promise((resolve) => {{
    const overlay = document.createElement('div');
    overlay.innerHTML = `
        <style>{_js_escape(_MODAL_STYLES)}</style>
        <div class="xnat-overlay">
            <div class="xnat-modal">
                <h3>{title}</h3>
                <label>Request payload</label>
                <pre>{payload_json}</pre>
                {status_section}
                <div class="btn-row">
                    <button id="xnat-done" class="primary">Done</button>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    document.getElementById('xnat-done').onclick = () => {{
        overlay.remove(); resolve(null);
    }};
}});
"""

    # ------------------------------------------------------------------
    # Notification helper
    # ------------------------------------------------------------------
    @staticmethod
    async def _notify(
        emitter: Callable[..., Awaitable[Any]] | None,
        message: str,
        error: bool = False,
    ) -> None:
        """Emit a status notification to the user."""
        if emitter:
            await emitter(
                {
                    "type": "status",
                    "data": {"description": message, "done": True},
                }
            )


# ------------------------------------------------------------------
# Utility
# ------------------------------------------------------------------
def _js_escape(text: str) -> str:
    """Escape a string for safe embedding inside a JS template literal."""
    return text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
