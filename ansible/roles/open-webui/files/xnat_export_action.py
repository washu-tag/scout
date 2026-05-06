"""
title: Send to XNAT
description: Export Scout query results to an XNAT instance. Adds a button to
             the message toolbar. Consumes the recipe + identity-list artifact
             written by search_reports (or load_id_list when accessions are
             present), de-dupes the accession_numbers, walks a project/IRB
             form, and POSTs to XNAT. Review of included/excluded reports
             happens upstream in the chat itself via read_reports — no review
             step in the Action.
author: Scout Team
version: 0.3.0
"""

from __future__ import annotations

import html
import json
import logging
from typing import Any, Awaitable, Callable

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


# Greyscale XNAT swoosh logo, traced from the official icon at wiki.xnat.org.
# Source SVG lives alongside this file at xnat_logo_grey.svg.
_ICON_DATA_URI = (
    "data:image/svg+xml;base64,"
    "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyMCIg"
    "aGVpZ2h0PSIyMCIgdmlld0JveD0iMCAwIDMyMCAzMjAiPgo8cGF0aCBkPSJNMCAwIEMt"
    "NC4wOSA4LjE3IC0xMS44NCAxNC43OCAtMTguMTIgMjEuMzggQy0yOS4yMiAzNC4xOCAt"
    "MzguMTIgNDguMzggLTM4LjE5IDY1Ljc1IEMtMzguMiA2Ny4yOSAtMzguMjIgNjguODMg"
    "LTM4LjIzIDcwLjQyIEMtMzguMDEgNzQuODggLTM3LjI0IDc4LjcyIC0zNiA4MyBDLTMx"
    "LjkyIDc5LjU1IC0yNy45NiA3Ni4wMyAtMjQuMDYgNzIuMzggQzAuNTMgNTAuMDMgNDku"
    "NDMgMTQgODQgMTQgQzgzLjQyIDE2LjIzIDgyLjg1IDE4LjQ1IDgyLjI1IDIwLjc1IEM3"
    "Ni41MiA0My43NyA3Mi41NiA2Ni45MSA3My4yNSA5MC43MSBDNzMuMjggOTIuNjEgNzMu"
    "MzEgOTQuNTIgNzMuMzQgOTYuNDggQzczLjQgMTAwLjQ0IDczLjUzIDEwNC4zOSA3My43"
    "MSAxMDguMzQgQzczLjc5IDEyMi4zOSA3My43OSAxMjIuMzkgNjguNzYgMTI2LjkxIEM2"
    "NC41OCAxMjkgNjAuNDQgMTMwLjUzIDU2IDEzMiBDNDQuNTkgMTM3LjkxIDMzLjIzIDE0"
    "My45NyAyMi4zMSAxNTAuNzUgQzIxLjAxIDE1MS41NSAxOS43MSAxNTIuMzUgMTguMzYg"
    "MTUzLjE3IEMxNiAxNTYgMTYgMTU2IDE2Ljk1IDE2MC4zNCBDMTcuMjggMTYxLjExIDE3"
    "LjI4IDE2MS4xMSAxOSAxNjUgQzM2LjU0IDIxNy43NCAxNy40OCAyNjcuMzggLTE4LjA2"
    "IDMwNy43NSBDLTI4LjY0IDMxOSAtMjguNjQgMzE5IC0zNSAzMTkgQy0zNy4zNiAzMTYu"
    "MjUgLTM3LjM2IDMxNi4yNSAtMzkuNzUgMzEyLjQ0IEMtNDAuNjIgMzExLjA2IC00MS40"
    "OSAzMDkuNjkgLTQyLjM4IDMwOC4yNyBDLTQzLjI1IDMwNi44NiAtNDQuMTEgMzA1LjQ1"
    "IC00NSAzMDQgQy00NS43OCAzMDIuNzUgLTQ2LjU2IDMwMS41MSAtNDcuMzcgMzAwLjIz"
    "IEMtNjMuMjggMjc0LjQgLTc4IDI0Mi44NyAtNzggMjEyIEMtNzkuNjUgMjExLjM0IC04"
    "MS4zIDIxMC42OCAtODMgMjEwIEMtOTkgMjAyIC05OSAyMDIgLTEwMi45NCAyMDAgQy0x"
    "MTEuMTIgMTk1Ljk3IC0xMTkuMiAxOTIuNTUgLTEyNy44NyAxODkuNzMgQy0xMjkuNjIg"
    "MTg5LjEyIC0xMzEuMzggMTg4LjUyIC0xMzMuMTkgMTg3LjkgQy0xMzYuNzYgMTg2LjY4"
    "IC0xNDAuMzYgMTg1LjUzIC0xNDMuOTggMTg0LjQ2IEMtMTUyLjM3IDE4MS41MiAtMTUy"
    "LjM3IDE4MS41MiAtMTU2IDE3OCBDLTE1NS43NiAxNzEuMzMgLTE1Mi43OSAxNjUuOTYg"
    "LTE1MCAxNjAgQy0xNDUuMDIgMTQwLjA4IC0xNDIuMSAxMjAuNDcgLTE0MSAxMDAgQy0x"
    "MTcuNjYgMTA5LjM0IC05NC4yNCAxMjcuOTcgLTc1IDE0NCBDLTc0LjM0IDE0NCAtNzMu"
    "NjggMTQ0IC03MyAxNDQgQy03MS4yNSAxNDAuODEgLTcxLjI1IDE0MC44MSAtNzEgMTM2"
    "IEMtNzMuMjQgMTMyLjgyIC03NS41MyAxMjkuNjUgLTc4LjAyIDEyNi42NiBDLTEwMy41"
    "MyA5NS4zNyAtMTIxIDUyLjk5IC0xMDkgMTMgQy0xMDguNjcgMTIuMzQgLTEwOC4zNCAx"
    "MS42OCAtMTA4IDExIEMtMTAyLjA3IDEwLjc2IC05Ni4xOCAxMC42MyAtOTAuMjUgMTAu"
    "NTYgQy02Mi4wNCAxMC4wMiAtMzQuNDggNy4wNyAtNi44OSAwLjg1IEMtMyAwIC0zIDAg"
    "MCAwIFogTS0yNSAxOTcgQy0yMy4zNyAyMDAuNDIgLTIxLjczIDIwMy44MyAtMTkuOTMg"
    "MjA3LjE1IEMtNy4zNiAyMzIuMjUgLTEzLjIgMjYxLjM2IC0yMC42IDI4Ny4wMSBDLTIx"
    "LjkgMjkxLjY1IC0yMyAyOTYuMjkgLTI0IDMwMSBDLTUuMzEgMjgyLjMxIC0zLjY2IDIz"
    "Ni43NCAtMTAgMjEzIEMtMTIuODIgMjA1LjY2IC0xNS45NiAxOTguNzQgLTIwIDE5MiBD"
    "LTIyIDE5MiAtMjIgMTkyIC0yNSAxOTcgWiBNLTQxIDI1MSBDLTQxLjY3IDI2OS4yMiAt"
    "MzguOTUgMjg3LjIyIC0zNSAzMDUgQy0zNC42NyAzMDUgLTM0LjM0IDMwNSAtMzQgMzA1"
    "IEMtMzMuMzEgMjY1LjYgLTMzLjMxIDI2NS42IC00MCAyNTEgQy00MC4zMyAyNTEgLTQw"
    "LjY2IDI1MSAtNDEgMjUxIFogTS0yNSAzMDEgQy0yNCAzMDMgLTI0IDMwMyAtMjQgMzAz"
    "IFoiIGZpbGw9IiM4ODgiIHRyYW5zZm9ybT0idHJhbnNsYXRlKDE5NiwxKSIvPgo8L3N2"
    "Zz4="
)

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
        min-width: 400px; max-width: 800px;
        max-height: 85vh; overflow: auto;
        box-shadow: 0 8px 32px rgba(0,0,0,0.3);
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 14px;
    }
    .xnat-modal h3 { margin: 0 0 16px; font-size: 18px; }
    .xnat-modal h4 { margin: 16px 0 8px; font-size: 14px; }
    .xnat-modal label {
        display: block; margin-bottom: 4px;
        font-weight: 600; font-size: 13px;
    }
    .xnat-modal select, .xnat-modal input[type=text], .xnat-modal textarea {
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
    .review-cards { display: flex; flex-direction: column; gap: 8px; max-height: 360px; overflow-y: auto; }
    .review-card {
        background: var(--color-surface-secondary, #2a2a2a);
        border-left: 3px solid #4caf50;
        padding: 8px 10px; border-radius: 4px; font-size: 12px;
    }
    .review-card.excluded { border-left-color: #e57373; }
    .review-card .ids { color: #aaa; font-size: 11px; margin-bottom: 4px; }
    .review-card .text { line-height: 1.4; max-height: 200px; overflow-y: auto; white-space: pre-wrap; }
    .review-section-empty { color: #888; font-style: italic; padding: 8px; }
"""


class Action:
    """Open WebUI Action that exports Scout query results to XNAT."""

    class Valves(BaseModel):
        xnat_base_url: str = Field(
            default="http://mock-xnat.scout-analytics.svc.cluster.local:8000",
            description="XNAT API base URL (internal, used for server-side requests).",
        )
        xnat_external_url: str = Field(
            default="https://xnat.example.org",
            description="XNAT external URL (browser-facing) shown in result links.",
        )
        send_timeout_seconds: int = Field(
            default=30,
            description="HTTP timeout in seconds for the XNAT API call",
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

        log.info("Send to XNAT action() called for chat %s", body.get("chat_id"))

        # -- Step 1: Find Scout query result JSON files for this chat -----
        user_id = __user__.get("id", "")
        files = await self._collect_json_files(body, user_id)
        log.info("Found %d Scout JSON files for chat", len(files))
        if not files:
            await self._notify(
                __event_emitter__,
                "No Scout query results to export for this chat",
                error=True,
            )
            return None

        # -- Step 2: Extract user identity ---------------------------------
        user_info = self._extract_user_info(__user__, __oauth_token__)

        # -- Multi-step flow: pick → form → confirm → send ----------------
        # No review step inside the Action — by the time the user clicks Send
        # to XNAT, the cohort has already been audited in chat via
        # read_reports calls on included/excluded samples.
        project_name = ""
        irb_number = ""
        comment = ""
        rationale = ""
        data_use_committee = ""
        accession_numbers: list[str] = []
        included_rows: list[dict] = []
        selected_file: dict = {}

        # Step A: file picker (skipped when there's only one file).
        if len(files) == 1:
            file_id = files[0]["id"]
            selected_file = files[0]
        else:
            picker = await __event_call__(
                {
                    "type": "execute",
                    "data": {"code": self._build_file_picker_js(files)},
                }
            )
            if not picker:
                return None
            if isinstance(picker, str):
                picker = json.loads(picker)
            file_id = picker.get("file_id", "")
            if not file_id:
                return None
            selected_file = next((f for f in files if f["id"] == file_id), files[0])

        # Step B: load + parse JSON, fail fast if unreadable.
        try:
            file_content = await self._read_file_content(file_id)
            payload_data = json.loads(file_content)
        except Exception as exc:
            log.warning("Failed to read JSON %s: %s", file_id, exc)
            await self._notify(
                __event_emitter__,
                f"Failed to read selected file: {exc}",
                error=True,
            )
            return None

        # Saved cohort shape per scout-tool-design.md §5: { recipe, rows: [...] }
        # where each row has message_control_id, accession_number, epic_mrn,
        # included (bool), and optional why_excluded / why_included excerpts.
        all_rows = payload_data.get("rows", []) or []
        included_rows = [r for r in all_rows if bool(r.get("included"))]
        # De-dupe accession_numbers — search artifacts may carry multiple
        # message_control_ids per accession; XNAT thinks at the accession level.
        seen: set[str] = set()
        accession_numbers: list[str] = []
        for row in included_rows:
            acc = str(row.get("accession_number") or "").strip()
            if acc and acc not in seen:
                seen.add(acc)
                accession_numbers.append(acc)
        if not accession_numbers:
            await self._notify(
                __event_emitter__,
                "Selected artifact has no included rows with accession_number. "
                "Run a search_reports query that SELECTs accession_number first.",
                error=True,
            )
            return None

        # Step D: project/IRB form (loops with confirmation).
        while True:
            form_result = await __event_call__(
                {
                    "type": "execute",
                    "data": {
                        "code": self._build_form_js(
                            file_name=selected_file["name"],
                            row_count=len(included_rows),
                            project_name=project_name,
                            irb_number=irb_number,
                            comment=comment,
                            rationale=rationale,
                            data_use_committee=data_use_committee,
                        )
                    },
                }
            )
            if not form_result:
                return None
            if isinstance(form_result, str):
                form_result = json.loads(form_result)
            project_name = form_result.get("project_name", "").strip()
            irb_number = form_result.get("irb_number", "").strip()
            comment = form_result.get("comment", "").strip()
            rationale = form_result.get("rationale", "").strip()
            data_use_committee = form_result.get("data_use_committee", "").strip()
            if not project_name:
                await self._notify(
                    __event_emitter__, "Project name is required", error=True
                )
                continue

            # Step E: confirmation.
            confirm_result = await __event_call__(
                {
                    "type": "execute",
                    "data": {
                        "code": self._build_confirmation_js(
                            file_name=selected_file["name"],
                            user_info=user_info,
                            project_name=project_name,
                            accession_numbers=accession_numbers,
                            row_count=len(included_rows),
                            irb_number=irb_number,
                            comment=comment,
                            rationale=rationale,
                            data_use_committee=data_use_committee,
                        )
                    },
                }
            )
            if not confirm_result:
                return None
            if isinstance(confirm_result, str):
                confirm_result = json.loads(confirm_result)
            ca = confirm_result.get("action", "cancel")
            if ca == "back":
                continue
            if ca == "send":
                break
            return None

        # -- Step 6: Send to XNAT -----------------------------------------
        xnat_url = f"{self.valves.xnat_base_url.rstrip('/')}/export"
        payload: dict[str, Any] = {
            "projectName": project_name,
            "requestUser": user_info.get("username", "")
            or user_info.get("name", "Unknown"),
            "data": [{"Accession Number": acc} for acc in accession_numbers],
        }
        if irb_number:
            payload["irbNumber"] = irb_number
        if comment:
            payload["comment"] = comment
        if rationale:
            payload["rationale"] = rationale
        if data_use_committee:
            payload["dataUseCommitteeOversight"] = data_use_committee

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

        # -- Step 7: Result modal -----------------------------------------
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
    async def _collect_json_files(body: dict, user_id: str) -> list[dict[str, str]]:
        """
        Find Scout cohort artifacts for the current chat. Match either the new
        `meta.scout_result` flag (search_reports / load_id_list) or the legacy
        `meta.scout_query_result` flag from the pre-design tool, so artifacts
        produced before the refactor still export. Sort newest first.
        """
        import inspect

        from open_webui.models.files import Files

        chat_id = body.get("chat_id", "")
        if not chat_id:
            return []
        all_files = Files.get_files_by_user_id(user_id)
        if inspect.isawaitable(all_files):
            all_files = await all_files
        matched = sorted(
            [
                {
                    "id": f.id,
                    "name": f.filename,
                    "created_at": f.created_at or 0,
                    "row_count": (f.meta or {}).get("row_count"),
                }
                for f in all_files
                if f.filename
                and f.filename.lower().endswith(".json")
                and (f.meta or {}).get("chat_id") == chat_id
                and (
                    (f.meta or {}).get("scout_result")
                    or (f.meta or {}).get("scout_query_result")
                )
            ],
            key=lambda f: f["created_at"],
            reverse=True,
        )
        return [
            {"id": f["id"], "name": f["name"], "row_count": f.get("row_count")}
            for f in matched
        ]

    @staticmethod
    def _extract_user_info(user: dict, oauth_token: dict) -> dict[str, str]:
        info: dict[str, str] = {
            "name": user.get("name", "Unknown"),
            "email": user.get("email", ""),
        }
        id_token = oauth_token.get("id_token", "")
        if id_token:
            try:
                import base64

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
                pass
        return info

    @staticmethod
    async def _read_file_content(file_id: str) -> str:
        import inspect

        from open_webui.models.files import Files
        from open_webui.storage.provider import Storage

        file_record = Files.get_file_by_id(file_id)
        if inspect.isawaitable(file_record):
            file_record = await file_record
        if not file_record:
            raise FileNotFoundError(f"File {file_id} not found")
        local_path = Storage.get_file(file_record.path)
        if inspect.isawaitable(local_path):
            local_path = await local_path
        with open(local_path, "r", encoding="utf-8") as f:
            return f.read()

    # ------------------------------------------------------------------
    # Modal JS builders
    # ------------------------------------------------------------------
    def _build_file_picker_js(self, files: list[dict[str, Any]]) -> str:
        """Step A: pick a Scout query result JSON file (only shown when >1)."""
        options = "".join(
            f'<option value="{_js_escape(f["id"])}">'
            f'{_js_escape(f["name"])}'
            f'{(" (" + str(f["row_count"]) + " rows)") if f.get("row_count") else ""}'
            f"</option>"
            for f in files
        )
        return f"""
return new Promise((resolve) => {{
    const overlay = document.createElement('div');
    overlay.innerHTML = `
        <style>{_js_escape(_MODAL_STYLES)}</style>
        <div class="xnat-overlay">
            <div class="xnat-modal">
                <h3>Send to XNAT &mdash; pick a result</h3>
                <p style="color:#aaa;font-size:13px;margin-bottom:12px;">
                  Choose which Scout query result to review and submit.
                  Most recent first.
                </p>
                <label>Results file</label>
                <select id="xnat-file">{options}</select>
                <div class="btn-row">
                    <button id="xnat-cancel">Cancel</button>
                    <button id="xnat-next" class="primary">Review &rarr;</button>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
    document.getElementById('xnat-cancel').onclick = () => {{
        overlay.remove(); resolve(null);
    }};
    document.getElementById('xnat-next').onclick = () => {{
        const fileId = document.getElementById('xnat-file').value;
        overlay.remove();
        resolve(JSON.stringify({{ file_id: fileId }}));
    }};
}});
"""

    def _build_form_js(
        self,
        file_name: str,
        row_count: int,
        project_name: str = "",
        irb_number: str = "",
        comment: str = "",
        rationale: str = "",
        data_use_committee: str = "",
    ) -> str:
        """Step D: project/IRB form (after the user has reviewed)."""
        # Pre-fill values for "Back" round-trips.
        return f"""
return new Promise((resolve) => {{
  try {{
    const overlay = document.createElement('div');
    overlay.innerHTML = `
        <style>{_js_escape(_MODAL_STYLES)}</style>
        <div class="xnat-overlay">
            <div class="xnat-modal">
                <h3>XNAT submission details</h3>
                <p style="color:#aaa;font-size:13px;margin-bottom:12px;">
                  Cohort: <b>{_js_escape(file_name)}</b> ({row_count} rows)
                </p>
                <label>XNAT project name (required)</label>
                <input type="text" id="xnat-project" placeholder="e.g. MyProject"
                       value="{_js_escape(project_name)}" />
                <div style="margin-top:8px;margin-bottom:4px;">
                    <button type="button" id="xnat-toggle-optional"
                        style="background:none;border:none;color:#60a5fa;cursor:pointer;font-size:13px;padding:0;">
                        &#9654; Optional details
                    </button>
                </div>
                <div id="xnat-optional" style="display:none;">
                    <label>IRB number</label>
                    <input type="text" id="xnat-irb" placeholder="e.g. 202400123"
                           value="{_js_escape(irb_number)}" />
                    <label>Comment</label>
                    <input type="text" id="xnat-comment" value="{_js_escape(comment)}" />
                    <label>Rationale</label>
                    <input type="text" id="xnat-rationale" value="{_js_escape(rationale)}" />
                    <label>Data Use Committee oversight</label>
                    <input type="text" id="xnat-duc" value="{_js_escape(data_use_committee)}" />
                </div>
                <div class="btn-row">
                    <button id="xnat-cancel">Cancel</button>
                    <button id="xnat-next" class="primary">Confirm &rarr;</button>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    const toggleBtn = document.getElementById('xnat-toggle-optional');
    const optionalDiv = document.getElementById('xnat-optional');
    let optionalOpen = false;
    toggleBtn.onclick = () => {{
        optionalOpen = !optionalOpen;
        optionalDiv.style.display = optionalOpen ? 'block' : 'none';
        toggleBtn.innerHTML = optionalOpen ? '&#9660; Optional details' : '&#9654; Optional details';
    }};

    document.getElementById('xnat-cancel').onclick = () => {{
        overlay.remove(); resolve(null);
    }};
    document.getElementById('xnat-next').onclick = () => {{
      try {{
        const result = JSON.stringify({{
            project_name: document.getElementById('xnat-project').value,
            irb_number: document.getElementById('xnat-irb').value,
            comment: document.getElementById('xnat-comment').value,
            rationale: document.getElementById('xnat-rationale').value,
            data_use_committee: document.getElementById('xnat-duc').value,
        }});
        overlay.remove();
        resolve(result);
      }} catch(e) {{
        overlay.remove();
        resolve(null);
      }}
    }};

    const inp = document.getElementById('xnat-project');
    if (inp) inp.focus();
  }} catch(e) {{
    console.error('[XNAT] Form setup error:', e);
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
        irb_number: str = "",
        comment: str = "",
        rationale: str = "",
        data_use_committee: str = "",
    ) -> str:
        user_display = _js_escape(user_info.get("name", "Unknown"))
        email = _js_escape(user_info.get("email", ""))
        user_line = f"{user_display} &lt;{email}&gt;" if email else user_display

        optional_lines: list[str] = []
        if irb_number:
            optional_lines.append(
                f"<div><strong>IRB number:</strong> {_js_escape(irb_number)}</div>"
            )
        if comment:
            truncated = comment[:200] + ("…" if len(comment) > 200 else "")
            optional_lines.append(
                f"<div><strong>Comment:</strong> {_js_escape(truncated)}</div>"
            )
        if rationale:
            truncated = rationale[:200] + ("…" if len(rationale) > 200 else "")
            optional_lines.append(
                f"<div><strong>Rationale:</strong> {_js_escape(truncated)}</div>"
            )
        if data_use_committee:
            optional_lines.append(
                f"<div><strong>Data Use Committee:</strong> "
                f"{_js_escape(data_use_committee)}</div>"
            )
        optional_section = "".join(optional_lines)

        accession_numbers = accession_numbers or []
        n_acc = len(accession_numbers)
        if n_acc > 0:
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
                    {optional_section}
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
        payload_json = _js_escape(json.dumps(payload, indent=2))
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
            resp = response_data or {}
            resp_status = _js_escape(str(resp.get("status", "")))
            resp_message = _js_escape(str(resp.get("message", "")))
            resp_id = resp.get("id")
            response_json = _js_escape(json.dumps(resp, indent=2))
            if resp_id is not None:
                link = (
                    f"{xnat_external_url.rstrip('/')}/xapi/iq/data-requests/{resp_id}"
                )
                link_html = (
                    f'<p><a href="{_js_escape(link)}" target="_blank" '
                    f'style="color:#60a5fa;">{_js_escape(link)}</a></p>'
                )
            else:
                link_html = ""
            status_section = (
                f'<div class="info-block">'
                f"<div><strong>Status:</strong> {resp_status}</div>"
                f"<div><strong>Message:</strong> {resp_message}</div>"
                + (
                    f"<div><strong>Request ID:</strong> {resp_id}</div>"
                    if resp_id is not None
                    else ""
                )
                + f"</div>"
                f"<label>Response</label>"
                f"<pre>{response_json}</pre>"
                f"{link_html}"
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
        if emitter:
            await emitter(
                {
                    "type": "status",
                    "data": {"description": message, "done": True},
                }
            )


# ------------------------------------------------------------------
# JS string escape helper
# ------------------------------------------------------------------
def _js_escape(text: str) -> str:
    """Escape a string for safe embedding inside a JS template literal."""
    return text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
