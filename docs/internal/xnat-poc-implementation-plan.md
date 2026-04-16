# XNAT Export POC Implementation Plan

## Goal

Build a proof-of-concept demonstrating data flowing from Scout's Open WebUI Chat to an XNAT instance. The POC has two parts:

1. **Scout Query Tool** — A native Open WebUI Tool function that replaces the Trino MCP tool. It stores query results as files and returns only summaries to the LLM context.
2. **Send to XNAT Action** — An Open WebUI Action function that adds a button to the chat UI. When clicked, it walks the user through selecting a results file, specifying an XNAT destination, and confirming the export.

Since there is no network access from preproduction Scout to XNAT and the XNAT API details are not yet finalized, the POC will mock the query results and the XNAT API call, focusing on demonstrating the end-to-end UI flow and data handling.

## Architecture Overview

```
User asks question about radiology data
    │
    ▼
LLM generates SQL (as it does today)
    │
    ▼
Scout Query Tool executes "query"
    ├── Returns canned CSV data (mock)
    ├── Stores full results as Open WebUI file (CSV, persisted server-side)
    ├── Returns summary to LLM context (row count, columns, sample)
    └── Renders Rich UI data table in iframe (with CSV download button)
    │
    ▼
LLM presents summary to user; Rich UI table is visible inline in chat
    │
    ▼
User clicks "Send to XNAT" button on message toolbar
    │
    ▼
XNAT Export Action
    ├── Step 1: Input form (file selection if ambiguous, XNAT project name)
    ├── Step 2: Confirmation (accession numbers, user identity, project)
    ├── POSTs JSON payload to Mock XNAT Service
    ├── Displays request and response to user
    └── Shows success with link to XNAT
    │
    ▼
Mock XNAT Service (FastAPI, in-cluster)
    ├── Receives POST request with JSON payload
    ├── Logs the request for observability
    └── Returns a JSON response (simulating XNAT acceptance)
```

## Files to Create or Modify

### New Files

| File | Purpose |
|------|---------|
| `ansible/roles/open-webui/files/scout_query_tool.py` | Scout Query Tool function |
| `ansible/roles/open-webui/files/scout_query_stub_data.csv` | Canned CSV result data (you fill in contents) |
| `ansible/roles/open-webui/files/xnat_export_action.py` | Send to XNAT Action function |
| `ansible/roles/open-webui/files/xnat_logo_grey.svg` | Greyscale XNAT logo for the action button |
| `ansible/roles/open-webui/files/mock_xnat_service.py` | Mock FastAPI service that stands in for the real XNAT API |
| `ansible/roles/open-webui/files/mock-xnat-deployment.yaml` | Kubernetes manifests to deploy the mock service in-cluster |

### Files to Modify

| File | Change |
|------|--------|
| `ansible/roles/open-webui/files/gpt-oss-scout-query-prompt.md` | Update system prompt to reference the new tool instead of Trino MCP |
| `ansible/roles/open-webui/README.md` | Add installation steps for the new tool and action |

## Deployment Note

> **Future improvement**: The current deployment pattern for Open WebUI extensions (filters, tools, actions) is manual copy-paste into the admin panel. This works but is error-prone and doesn't scale. A future improvement would be to automate extension deployment via the Open WebUI REST API (`POST /api/v1/functions/create/`) from Ansible, similar to how the model creation Job works today. This is out of scope for the POC but worth revisiting.

---

## Phase 1: Scout Query Tool — COMPLETE

This is a native Open WebUI Tool function (class `Tools`) that replaces the Trino MCP tool. The LLM invokes it to run SQL queries against Scout's data.

**Status**: Implemented and tested. The tool is working in Open WebUI with mock data, Rich UI table rendering, and CSV download.

### What was built

**File**: `ansible/roles/open-webui/files/scout_query_tool.py`

- Class `Tools` with a single LLM-callable method `query(sql)`
- `Valves` for Trino connection settings + `use_mock_data` toggle (defaults to `True`)
- Mock mode: parses embedded `MOCK_DATA_CSV` string constant, ignores actual SQL
- Stores results server-side via `Storage.upload_file()` + `Files.insert_new_file()` (Open WebUI internal APIs)
- Returns a tuple of `(HTMLResponse, summary_string)`:
  - The `HTMLResponse` renders as a Rich UI iframe showing an interactive data table with a "Download CSV" button (CSV is base64-encoded inline)
  - The `summary_string` is what the LLM receives in context (row count, columns, sample rows)
- Status events show "Executing query..." / "Query complete" shimmer
- Trino implementation is stubbed with `NotImplementedError`; `use_mock_data` must be `True` for now
- Uses only the `csv` standard library module (no pandas dependency)
- Dark theme styling matching Open WebUI's default appearance

**File**: `ansible/roles/open-webui/files/scout_query_stub_data.csv`

Separate CSV file for readability/version control. Must be copied into the `MOCK_DATA_CSV` constant in the Python file. Rows need to be filled in with representative data.

**File**: `ansible/roles/open-webui/files/gpt-oss-scout-query-prompt.md`

Updated system prompt: references "Scout Query Tool" instead of "Trino MCP", adds rules about referencing summaries rather than reproducing data, and mentions the "Send to XNAT" button.

**File**: `ansible/roles/open-webui/README.md`

Updated installation steps for the new tool.

### Lessons learned during implementation

1. **Tools vs Functions are separate upload paths.** Tools (class `Tools`) must be uploaded via **Workspace > Tools**. Functions (class `Filter`, `Action`, `Pipe`) go via **Admin Panel > Functions**. Using the wrong path gives a confusing "No Function class found" or "No Tools class found" error.

2. **`Storage.upload_file()` requires a `tags` parameter.** Signature is `upload_file(file: BinaryIO, filename: str, tags: Dict[str, str])`. The `tags` dict is only used by S3 (when `S3_ENABLE_TAGGING` is true), but all storage providers require it. Pass `{}` for local storage.

3. **The `files` event emitter does not produce visible UI.** We emitted `{"type": "files", "data": {"files": [...]}}` correctly and the file was persisted server-side (accessible via Open WebUI Files API), but no file attachment appeared in the chat UI. The exact event schema for visible file attachments remains unclear. This is not a blocker because the Rich UI table with embedded CSV download provides a better UX anyway.

4. **Rich UI is the primary data display mechanism.** Returning `(HTMLResponse, summary_string)` works well: the iframe renders inline at the tool call indicator, the LLM only sees the compact summary. All CSS/JS must be inline (no external CDN) due to Scout's CSP middleware.

5. **Rich UI iframes are sandboxed.** They lack `allow-same-origin` by default, meaning they cannot make fetch/XHR requests to Open WebUI or other Scout services. They also cannot escape the CSP `connect-src 'self'` restriction to reach other subdomains. This means export-to-XNAT logic cannot live in the iframe — it must be in a server-side Action function.

6. **No pandas needed.** The `csv` standard library module handles all CSV parsing and generation. Avoids dependency management issues.

### Installation (documented in README)

1. Navigate to **Workspace > Tools > New Tool**
2. Set Tool ID: `scout_query_tool`
3. Paste contents of `scout_query_tool.py`
4. Save; configure Valves (`use_mock_data: true`)
5. Edit the Scout Explorer model:
   - Remove Trino MCP from tools
   - Enable Scout Query Tool
   - Ensure Function Calling is set to "Native"
6. Update the model's system prompt with the revised `gpt-oss-scout-query-prompt.md`
7. Test: Ask "Show me CT studies from 2024" — should see a Rich UI table and the LLM referencing a summary

---

## Phase 2: Send to XNAT Action — COMPLETE

This is an Open WebUI Action function (class `Action`) that adds a "Send to XNAT" button to the message toolbar.

**Status**: Implemented and tested end-to-end. The multi-step dialog flow (input form → confirmation → send → result) works, including file reading, accession number extraction, payload construction, and mock XNAT service integration.

### What was built

**File**: `ansible/roles/open-webui/files/xnat_export_action.py`

- Class `Action` with a multi-step dialog flow using `__event_call__` execute events
- `Valves` for `xnat_base_url` (internal API endpoint), `xnat_external_url` (browser-facing link), service account credentials, timeout, and debug toggle
- Step 1: Input form — file selection dropdown (all user's CSV files, sorted by recency), XNAT project name text input
- Step 2: File reading — reads CSV via `Files.get_file_by_id()` + `Storage.get_file()` (returns local path) + `open()`, extracts accession numbers from `accession_number` column
- Step 3: Confirmation dialog — shows user identity (from `__user__` or OAuth token), file name with row count, destination project, and accession numbers in a two-column monospace grid
- Step 4: HTTP POST to XNAT — sends JSON payload with `project_id`, `requested_by`, `accession_numbers`, `total_count`, `source`, `timestamp`
- Step 5: Result dialog — shows request payload, HTTP status, response JSON, and a clickable link using the external XNAT URL
- Back button loops to input form; Cancel aborts at any step
- Error handling for missing files, missing accession column, HTTP errors, and network failures

**File**: `ansible/roles/open-webui/files/mock_xnat_service.py`

- Standalone FastAPI reference implementation (for local development/testing)
- Single POST `/export` endpoint that echoes key fields in a success envelope (HTTP 202)
- GET `/health` endpoint for Kubernetes probes

**File**: `ansible/roles/open-webui/files/mock-xnat-deployment.yaml`

- Kubernetes ConfigMap + Deployment + Service manifest
- Uses the Open WebUI container image (has FastAPI/uvicorn pre-installed, avoids pip on air-gapped clusters)
- Mounts the service code from ConfigMap, runs uvicorn on port 8000
- ClusterIP Service at `mock-xnat.<namespace>.svc.cluster.local:8000`

**File**: `ansible/roles/open-webui/files/xnat_logo_grey.svg`

- Greyscale SVG icon file, vector-traced from the official XNAT icon at wiki.xnat.org
- Flat grey (#888) silhouette of the XNAT swoosh emblem, suitable for use as a 20px action button icon
- Embedded in the action as a base64 data URI via `_ICON_DATA_URI`

### Lessons learned during implementation

5. **`Storage.get_file()` returns a filesystem path, not file content.** The method signature is `get_file(file_path: str) -> str` and it returns the local path where the file is stored. To read content: `open(Storage.get_file(record.path), "r")`. This is different from what the method name suggests.

6. **Air-gapped clusters cannot pip install.** The `python:3.12-slim` image cannot reach PyPI. Using the Open WebUI image (`ghcr.io/open-webui/open-webui`) as the base for the mock service works because it already has FastAPI and uvicorn installed. Alternatively, use the Nexus proxy configured per ADR 0017.

7. **`__event_call__` returns strings, not dicts.** When the JS `resolve()` is called with `JSON.stringify({...})`, the Python side receives a string. Parse with `json.loads()` before accessing keys.

8. **Separate internal and external URLs.** The action needs two URL concepts: an internal `xnat_base_url` for server-side HTTP requests (in-cluster service name), and an `xnat_external_url` for browser-facing links shown to users. These are configured as separate Valves.

9. **Files must be scoped to chats via `meta.chat_id`.** Open WebUI's `File` model has no `chat_id` column — there is no database-level file-to-chat association. `Files.get_files_by_user_id()` returns ALL files the user has ever created across all chats. There is also no `Chats.insert_chat_files()` or `Chats.get_chat_files_by_chat_id_and_message_id()` API. The solution is to store `chat_id` in the file's `meta` JSON field when the tool creates it (the tool receives `__chat_id__` as an injected parameter), then filter by `meta.chat_id` in the action (using `body["chat_id"]` from the frontend). The `files` event emitter does persist file references in the chat's message history on the frontend, but the action body's messages do not include `files` arrays.

### Step 2.1: Prepare the XNAT logo icon — COMPLETE

**File**: `ansible/roles/open-webui/files/xnat_logo_grey.svg`

**Status**: Done. The XNAT swoosh icon was sourced from the official icon at `wiki.xnat.org`, vector-traced using `vtracer` into a simplified flat-fill SVG, and converted to greyscale (#888). The resulting SVG is ~2KB and embedded as a base64 data URI (Option A) in `_ICON_DATA_URI`.

**Approach taken**: Option A — data URI in the `icon_url` field. The SVG is a single-path silhouette of the XNAT swoosh emblem at 20x20 rendered size with a 320x320 viewBox. It matches Open WebUI's monochrome button aesthetic and works in both light and dark modes.

### Step 2.2: Create the XNAT Export Action function

**File**: `ansible/roles/open-webui/files/xnat_export_action.py`

#### Frontmatter

```python
"""
title: Send to XNAT
description: Export Scout query results to an XNAT instance. Adds a button to the message toolbar.
author: Scout Team
version: 0.1.0
"""
```

No external pip requirements expected — uses only `json`, `csv`, `io`, `base64`, standard library plus Open WebUI internals.

#### Class structure

```python
class Action:
    class Valves(BaseModel):
        xnat_base_url: str = Field(
            default="http://mock-xnat.scout.svc.cluster.local:8000",
            description="XNAT API base URL. For POC, points to the mock XNAT service."
        )
        xnat_service_account_id: str = Field(
            default="",
            description="XNAT service account ID for API access"
        )
        xnat_service_account_secret: str = Field(
            default="",
            description="XNAT service account secret"
        )
        priority: int = Field(
            default=0,
            description="Button order priority (lower = leftmost)"
        )

    actions = [
        {
            "id": "send_to_xnat",
            "name": "Send to XNAT",
            "icon_url": "data:image/svg+xml;base64,..."  # Greyscale XNAT logo
        },
    ]

    def __init__(self):
        self.valves = self.Valves()
```

#### The `action()` method

```python
async def action(
    self,
    body: dict,
    __user__: dict = {},
    __event_emitter__: Callable = None,
    __event_call__: Callable = None,
    __metadata__: dict = {},
    __request__ = None,
    __id__: str = None,
    __oauth_token__: dict = {},
) -> Optional[dict]:
```

#### Implementation flow

The action method orchestrates a multi-step dialog using `__event_call__` with `execute` type events for custom HTML/JS forms.

```
1. Collect files from conversation
   - Scan body["messages"] for file attachments
   - Filter to CSV files (from the Scout Query Tool)
   - If no files found: show notification "No query results to export" and return
   - Build file list: [{id, name, message_index}, ...]

2. Extract user identity from __oauth_token__
   - Decode the id_token JWT (base64 decode the payload; no signature verification needed)
   - Extract: name, email, preferred_username
   - Fallback: use __user__["name"] and __user__["email"] if token unavailable

3. Show input form (Step 1) via __event_call__ execute
   - If single file: pre-select it, show filename as read-only
   - If multiple files: dropdown to select which file
   - Text input: XNAT project name
   - "Next" and "Cancel" buttons
   - Returns: {file_id, project_name} or null if cancelled

4. Read the selected file
   - Fetch file content via Open WebUI Files API / Storage provider
   - Parse CSV; look for 'accession_number' column (case-insensitive)
   - If column not found: show error notification and return
   - Extract accession numbers, count them

5. Show confirmation dialog (Step 2) via __event_call__ execute
   - Display:
     - User: name <email> (from OAuth token)
     - Source file: filename (N rows)
     - Destination: XNAT project name
     - Accession numbers: first 10 + "... and N more" if truncated
   - "Back", "Send", and "Cancel" buttons
   - If "Back": loop back to step 3 (re-show input form)
   - Returns: {action: "send" | "back" | "cancel"}

6. Build and send XNAT API payload
   - Construct a JSON request payload (exact schema TBD — will match real XNAT API once known):
     {
         "project_id": project_name,
         "requested_by": {
             "name": user_name,
             "email": user_email,
             "username": preferred_username
         },
         "accession_numbers": [...],
         "total_count": N,
         "source": "scout-open-webui",
         "timestamp": ISO-8601
     }
   - POST the payload to the Mock XNAT Service endpoint (URL configured in Valves)
   - The mock service returns a JSON response (exact schema TBD)
   - If the request fails (network error, non-2xx), show an error notification and return

7. Show result (Step 3) via __event_call__ execute
   - Title: "XNAT Export — Sent"
   - Show the request payload in a formatted code block
   - Show the response from the mock service in a second formatted code block
   - Show a (non-functional) link: "{xnat_base_url}/data/projects/{project_name}"
   - "Done" button
   - Returns when user dismisses
```

#### Custom HTML/JS dialog implementation

Each dialog step is an `__event_call__` with type `execute`. The JS code creates a modal overlay, renders the form, and resolves a Promise with the user's input.

Template pattern:

```python
result = await __event_call__({
    "type": "execute",
    "data": {
        "code": f"""
            return new Promise((resolve) => {{
                const overlay = document.createElement('div');
                overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999';
                overlay.innerHTML = `
                    <div style="background:var(--color-surface-primary, white);color:var(--color-text-primary, black);padding:24px;border-radius:12px;min-width:400px;max-width:600px;max-height:80vh;overflow:auto;box-shadow:0 8px 32px rgba(0,0,0,0.3)">
                        <h3 style="margin:0 0 16px">Send to XNAT</h3>
                        <!-- form content here, templated from Python -->
                        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
                            <button id="xnat-cancel" style="...">Cancel</button>
                            <button id="xnat-next" style="...">Next</button>
                        </div>
                    </div>
                `;
                document.body.appendChild(overlay);

                document.getElementById('xnat-cancel').onclick = () => {{
                    overlay.remove();
                    resolve(null);
                }};
                document.getElementById('xnat-next').onclick = () => {{
                    overlay.remove();
                    resolve({{ /* collected values */ }});
                }};
            }});
        """
    }
})
```

**Styling note**: Use `var(--color-surface-primary, white)` and `var(--color-text-primary, black)` CSS variables where possible — Open WebUI defines these for its theming. The fallback values handle cases where the variables aren't available. This gives reasonable dark/light mode support without extra effort.

**HTML/JS as embedded strings**: Since Open WebUI functions are single-file, the HTML/JS for each dialog step must be embedded as Python strings. To keep things manageable:
- Build each dialog as a helper method that returns the JS code string
- Use Python f-strings to inject dynamic values (file lists, accession counts, user info)
- Keep the HTML simple — styled divs, not a full framework
- Escape curly braces (`{{` / `}}`) for f-string compatibility with JS

Example helper structure:

```python
def _build_input_form_js(self, files: list, user_info: dict) -> str:
    file_options = "".join(
        f'<option value="{f["id"]}">{f["name"]}</option>' for f in files
    )
    return f"""
        return new Promise((resolve) => {{
            // ... create overlay with file dropdown and project input
        }});
    """

def _build_confirmation_js(self, file_name, accessions, user_info, project) -> str:
    # ...

def _build_result_js(self, payload_json, xnat_url, project) -> str:
    # ...
```

### Step 2.3: File content retrieval in the action

The action needs to read the contents of the selected CSV file. Two approaches:

**Approach A — Open WebUI internal API (recommended)**:
```python
from open_webui.models.files import Files
from open_webui.storage.provider import Storage

file_record = Files.get_file_by_id(file_id)
file_path = file_record.path
content_bytes = Storage.get_file(file_path)
```

**Approach B — HTTP request to self**:
```python
import httpx
response = await httpx.AsyncClient().get(
    f"http://localhost:8080/api/v1/files/{file_id}/content",
    headers={"Authorization": f"Bearer {token}"}
)
```

Go with Approach A — it avoids auth token management and network round-trips.

### Step 2.4: Mock XNAT Service

**File**: `ansible/roles/open-webui/files/mock_xnat_service.py`

A lightweight FastAPI application that stands in for the real XNAT API. The action's "Send" step POSTs to this service and displays the response, making the demo feel like a real integration rather than a purely client-side mock.

#### What it does

- Exposes a single POST endpoint (path TBD — will mirror the real XNAT API route once known)
- Accepts a JSON body (schema TBD)
- Logs the incoming request for observability / demo purposes
- Returns a JSON response (schema TBD — will mirror real XNAT response once known)
- No authentication, no persistence, no validation beyond basic JSON parsing

#### Implementation notes

- Single-file FastAPI app, runnable with `uvicorn mock_xnat_service:app`
- No external dependencies beyond `fastapi` and `uvicorn`
- The request and response schemas are placeholders. Once the real XNAT API details are known, update them to match. For now, the service accepts whatever JSON is posted and returns a success envelope with an echo of key fields.
- Keep it minimal — this exists only to make the demo round-trip real

#### Deployment

**File**: `ansible/roles/open-webui/files/mock-xnat-deployment.yaml`

A simple Kubernetes Deployment + Service manifest:
- Single-replica Deployment running `uvicorn` in a lightweight Python container
- ClusterIP Service so the Open WebUI action can reach it by service name (e.g., `http://mock-xnat.<namespace>.svc.cluster.local:8000/...`)
- No ingress needed — this is internal-only, called server-side from the action

For the POC, this can be deployed manually with `kubectl apply`. No Ansible role or Helm chart needed.

#### Valve configuration

The XNAT Export Action's `xnat_base_url` Valve should point to the mock service's in-cluster URL for the POC (e.g., `http://mock-xnat.<namespace>.svc.cluster.local:8000`). In production, this would be swapped to the real XNAT URL.

### Step 2.5: Install and configure

Manual steps (add to README):

1. Navigate to Admin Panel > Functions > Create New Function (Actions are Functions, not Tools)
2. Set Function ID: `xnat_export_action`
3. Paste contents of `xnat_export_action.py`
4. Save; configure Valves:
   - `xnat_base_url`: The target XNAT instance URL
   - `xnat_service_account_id` and `xnat_service_account_secret`: Leave empty for POC (mock mode)
5. Enable the action:
   - Either toggle Global (appears on all models)
   - Or edit the Scout Explorer model and enable it there
6. Test: Generate query results, then click "Send to XNAT" on the message

---

## Phase 3: System Prompt Update

**File**: `ansible/roles/open-webui/files/gpt-oss-scout-query-prompt.md`

The system prompt needs to be updated to match the new tool interface. Key changes:

### Reference the new tool

```markdown
You have access to **Scout Query Tool** for querying the Scout Delta Lake.
```

### Update the rules

```markdown
## Rules

- **Always use the query tool** — Use Scout Query Tool to answer data questions; never fabricate data
- **Always filter by time** — Use `year` partition to avoid scanning millions of rows
- **Use LIMIT** — Especially for exploratory queries
- **Count in SQL when applicable** — Count in SQL rather than locally
- **Scout first if zero results** — Check distinct values and adjust criteria
- **Results are saved as files** — Query results are automatically saved as CSV files that the user can see and download. You receive a summary with row count, columns, and a few sample rows. Reference the summary when discussing results; do not reproduce the full data in your response.
```

### Update response guidelines

```markdown
## Response Guidelines

1. **Use diagnoses for clinical questions** — conditions, diseases, indications
2. **Use report text for imaging findings** — what radiologists described
3. **Present results clearly** — do NOT show SQL unless asked
4. **Reference, don't reproduce** — The user has the full data as a file attachment. Summarize insights rather than listing every row.
5. **Suggest export** — If the user has query results and might want to share them, mention that they can use the "Send to XNAT" button.
```

The SQL patterns, example queries, and troubleshooting sections remain unchanged — the LLM still writes Trino SQL, the tool just handles execution and result storage differently.

---

## Phase 4: Integration Testing

### Test 1: Basic query flow
1. Open a new chat with Scout Explorer
2. Ask: "Show me CT studies from 2024"
3. Verify:
   - LLM generates SQL and invokes the Scout Query Tool
   - A CSV file appears as an attachment on the message
   - The LLM's response references the summary (row count, columns) without reproducing all the data
   - [Stretch] A Rich UI table is visible in the chat

### Test 2: XNAT export — happy path
1. After Test 1, click "Send to XNAT" on the message with results
2. Verify:
   - Input form appears with the file pre-selected and a project name field
   - Enter a project name, click Next
   - Confirmation shows: user identity, file name, accession number sample and count, project name
   - Click Send
   - Request is POSTed to the mock XNAT service
   - Result dialog shows both the request payload and the mock service's response
   - A link to the (mock) XNAT project is shown

### Test 3: XNAT export — back flow
1. In the confirmation step, click Back
2. Verify: returns to the input form with previous values preserved
3. Change the project name, proceed through confirmation again

### Test 4: XNAT export — no file
1. On a message with no file attachments, click "Send to XNAT"
2. Verify: notification saying "No query results to export"

### Test 5: XNAT export — missing accession number column
1. If possible, produce a file without an `accession_number` column
2. Click "Send to XNAT"
3. Verify: error message about missing required column

### Test 6: XNAT export — mock service unavailable
1. Scale the mock XNAT service to 0 replicas (or misconfigure the Valve URL)
2. Go through the export flow and click Send
3. Verify: error notification about the request failing (network error / non-2xx)

### Test 7: Multiple files in conversation
1. Ask two separate queries to generate two file attachments
2. Click "Send to XNAT" on the message with both files (or on any message)
3. Verify: file selection dropdown appears, can choose which file to export

---

## Implementation Order

| Step | Description | Depends On | Status | Files |
|------|-------------|------------|--------|-------|
| 1 | Create stub CSV data | — | **DONE** | `scout_query_stub_data.csv` |
| 2 | Create Scout Query Tool (mock mode + Rich UI) | Step 1 | **DONE** | `scout_query_tool.py` |
| 3 | Update system prompt | — | **DONE** | `gpt-oss-scout-query-prompt.md` |
| 4 | Install tool, update model config, test query flow | Steps 2, 3 | **DONE** | README update |
| 5 | Prepare XNAT logo SVG | — | **DONE** | `xnat_logo_grey.svg` (traced from official XNAT icon) |
| 6 | Create XNAT Export Action (input form + confirmation + send) | Step 5 | **DONE** | `xnat_export_action.py` |
| 7 | ~~Add confirmation dialog to action~~ | — | **DONE** | (merged into step 6) |
| 8 | Create Mock XNAT Service | — | **DONE** | `mock_xnat_service.py`, `mock-xnat-deployment.yaml` |
| 9 | Deploy Mock XNAT Service to cluster | Step 8 | **DONE** | `kubectl apply` |
| 10 | ~~Add API call to mock service and result display~~ | — | **DONE** | (merged into step 6) |
| 11 | Install action, test full flow | Steps 4, 10 | **DONE** | README update |
| 12 | Fill in stub CSV with representative data | — | **DONE** | `scout_query_stub_data.csv`, `scout_query_tool.py` |
| 13 | Design or source a proper XNAT logo icon | — | **DONE** | `xnat_logo_grey.svg`, `xnat_export_action.py` `_ICON_DATA_URI` |
| 14 | Update XNAT API request/response to match real schema | — | **DONE** | `xnat_export_action.py`, `mock_xnat_service.py`, `mock-xnat-deployment.yaml` |

### Step 14: Update XNAT API request/response schema

The current action sends an empty-ish payload and shows the raw mock response. This step updates both to match the real XNAT Image Query (IQ) data request API.

#### Request body

```json
{
    "projectName": "...",
    "irbNumber": "...",
    "comment": "...",
    "requestUser": "...",
    "rationale": "...",
    "dataUseCommitteeOversight": "...",
    "data": [
        {"Accession Number": "ACC001"},
        {"Accession Number": "ACC002"}
    ]
}
```

Field mapping:

| Field | Source | UI input |
|-------|--------|----------|
| `projectName` | Input form | **Existing** — already collected as "XNAT project name" |
| `requestUser` | `__user__` / OAuth token | **Existing** — use `preferred_username` (or fallback to `__user__["name"]`) |
| `irbNumber` | Input form | **New** — optional text input on the input form |
| `comment` | Input form | **New** — optional text input (or textarea) on the input form |
| `rationale` | Input form | **New** — optional text input (or textarea) on the input form |
| `dataUseCommitteeOversight` | Input form | **New** — optional text input on the input form |
| `data` | CSV file | **Existing** — accession numbers already extracted; restructure from `["ACC001", ...]` to `[{"Accession Number": "ACC001"}, ...]` |

#### Changes to the input form dialog

Add optional fields below the existing project name input:

- **IRB number** — text input, optional, placeholder "e.g. 202400123"
- **Comment** — text input, optional
- **Rationale** — text input, optional
- **Data Use Committee oversight** — text input, optional

These fields should be visually grouped (e.g. under a "Optional details" label or collapsible section) so the form doesn't feel heavy for quick exports. All are optional — the user can leave them blank and the fields will be omitted or sent as empty strings.

#### Changes to the confirmation dialog

Show the new fields if provided (skip if empty):

- IRB number
- Comment (truncated if long)
- Rationale (truncated if long)
- Data Use Committee oversight

#### Changes to the payload construction

Replace the current payload builder with the new schema. The `data` array should be constructed as:

```python
"data": [{"Accession Number": acc} for acc in accession_numbers]
```

#### Expected response

```json
{
    "id": 86,
    "requestUser": "...",
    "requestDate": 1776281971235,
    "status": "PRE_PROCESSING",
    "deidStatus": "IDENTIFIABLE",
    "comment": "...",
    "message": "Request is being pre-processed.",
    "projectName": "...",
    "irbNumber": "...",
    "numberOfItemsRequested": 0,
    "numberOfItemsPreviouslyDownloaded": 0,
    "hasIrbProtocolForm": false
}
```

#### Changes to the result dialog

- Show `status` and `message` from the response prominently
- Show the request ID (`id`)
- Update the project link to: `{xnat_external_url}/xapi/iq/data-requests/{id}` (using the `id` from the response, not the project name)
- If the response has no `id` (error case), fall back to showing the raw response JSON

#### Changes to the mock XNAT service

Update the mock service to:
- Accept the new request body schema
- Return a response matching the expected schema above (with a random `id`, current timestamp for `requestDate`, `status: "PRE_PROCESSING"`, etc.)
- Update the ConfigMap in the deployment manifest to match

---

## Open Questions and Risks

### Resolved Questions

1. ~~**File event format**~~: **Resolved.** The `files` event emitter persists the file server-side but does not produce visible UI in the chat. This is not a blocker — the Rich UI table with embedded CSV download provides a better UX for displaying data. The server-side file is still accessible via the Open WebUI Files API, which the XNAT Export Action can use to read file contents.

2. ~~**pandas availability**~~: **Resolved.** The `csv` standard library module handles all needs. No external dependencies required.

3. ~~**CSP restrictions on Rich UI**~~: **Resolved.** Inline CSS/JS works. All Rich UI content is self-contained with no external resources. The base64-encoded CSV download also works within the sandbox.

4. ~~**Native function calling mode**~~: **Resolved.** Native mode works correctly with `__event_emitter__` status events and the Rich UI `(HTMLResponse, str)` return pattern. No flickering observed.

### Remaining Open Questions

1. **`__event_call__` timeout**: Default is 300 seconds per call. Complex confirmation dialogs (user reading through accession numbers) could hit this if the user walks away. The `WEBSOCKET_EVENT_CALLER_TIMEOUT` env var can increase this. Worth noting in the README.

2. **Action button on all messages**: Open WebUI shows action buttons on every assistant message, not just ones with file attachments. The action must gracefully handle being clicked on a message with no relevant files.

3. **File content access in Action**: The Action needs to read CSV files stored by the Query Tool. Plan is to use `Files.get_file_by_id()` + `Storage.get_file()` (Open WebUI internal APIs). These may change between versions. The alternative (`/api/v1/files/{id}/content` REST endpoint) is more stable but requires auth token management.

4. **Rich UI iframe sandbox limitations**: The iframe lacks `allow-same-origin` and is constrained by CSP `connect-src 'self'`. It cannot make HTTP requests to Open WebUI APIs or other Scout subdomains. This means XNAT export logic cannot live in the iframe — confirmed that the Action approach (server-side Python) is the correct architecture for the export button.

### Risks

1. **Open WebUI version sensitivity**: Native Tool functions depend on internal APIs (`open_webui.models.files`, `open_webui.storage.provider`). The `Storage.upload_file()` signature already required adjustment (missing `tags` parameter). These APIs may change between versions. Mitigation: pin Open WebUI version; test after upgrades; the `kubectl exec ... help(Storage.upload_file)` pattern documented in the code helps debug signature changes.

2. **`execute` event browser compatibility**: The custom HTML/JS modal approach runs unsandboxed JS in the page context via `new Function()`. This is powerful but fragile — Open WebUI UI updates could break assumptions about DOM structure or CSS variables. Mitigation: use minimal, self-contained styles; don't depend on Open WebUI's internal DOM structure.

3. **Tools vs Functions upload confusion**: Open WebUI has separate interfaces for Tools (Workspace > Tools) and Functions (Admin Panel > Functions). Using the wrong one gives cryptic errors. This is documented in the README but remains a usability trap for anyone setting up the system.
