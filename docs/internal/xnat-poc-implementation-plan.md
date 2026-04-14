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
    ├── Stores full results as Open WebUI file (CSV attachment on message)
    ├── Returns summary to LLM context (row count, columns, sample)
    └── [Stretch] Renders Rich UI data table in iframe
    │
    ▼
LLM presents summary to user; file is visible as attachment
    │
    ▼
User clicks "Send to XNAT" button on message toolbar
    │
    ▼
XNAT Export Action
    ├── Step 1: Input form (file selection if ambiguous, XNAT project name)
    ├── Step 2: Confirmation (accession numbers, user identity, project)
    ├── Builds JSON payload that would be sent to XNAT
    ├── Displays payload to user as proof-of-work
    └── Shows success with link to XNAT
```

## Files to Create or Modify

### New Files

| File | Purpose |
|------|---------|
| `ansible/roles/open-webui/files/scout_query_tool.py` | Scout Query Tool function |
| `ansible/roles/open-webui/files/scout_query_stub_data.csv` | Canned CSV result data (you fill in contents) |
| `ansible/roles/open-webui/files/xnat_export_action.py` | Send to XNAT Action function |
| `ansible/roles/open-webui/files/xnat_logo_grey.svg` | Greyscale XNAT logo for the action button |

### Files to Modify

| File | Change |
|------|--------|
| `ansible/roles/open-webui/files/gpt-oss-scout-query-prompt.md` | Update system prompt to reference the new tool instead of Trino MCP |
| `ansible/roles/open-webui/README.md` | Add installation steps for the new tool and action |

## Deployment Note

> **Future improvement**: The current deployment pattern for Open WebUI extensions (filters, tools, actions) is manual copy-paste into the admin panel. This works but is error-prone and doesn't scale. A future improvement would be to automate extension deployment via the Open WebUI REST API (`POST /api/v1/functions/create/`) from Ansible, similar to how the model creation Job works today. This is out of scope for the POC but worth revisiting.

---

## Phase 1: Scout Query Tool

This is a native Open WebUI Tool function (class `Tools`) that replaces the Trino MCP tool. The LLM invokes it to run SQL queries against Scout's data.

### Step 1.1: Create stub CSV data file

**File**: `ansible/roles/open-webui/files/scout_query_stub_data.csv`

Create a CSV file with columns that are representative of real Scout query results and include an `accession_number` column (required by the XNAT export flow). Suggested columns based on the existing reports schema:

```csv
accession_number,epic_mrn,modality,service_name,message_dt,patient_age,sex,report_section_impression
```

You will fill in the actual rows. Aim for 20-50 rows — enough to demonstrate pagination and summarization without being unwieldy.

**Acceptance criteria**: CSV file exists with headers and placeholder rows.

### Step 1.2: Create the Scout Query Tool function

**File**: `ansible/roles/open-webui/files/scout_query_tool.py`

#### Frontmatter

```python
"""
title: Scout Query Tool
description: Execute SQL queries against Scout Delta Lake. Replaces Trino MCP with native file storage and context-aware result handling.
author: Scout Team
version: 0.1.0
requirements: pandas
"""
```

The `requirements` field tells Open WebUI to pip-install these packages. `pandas` is useful for CSV handling and generating summaries. Check whether `pandas` is already available in the Open WebUI container; if so, the requirement is a no-op. If pip install is disabled in the environment, we may need to use only the standard library (`csv` module) instead.

#### Class structure

```python
class Tools:
    class Valves(BaseModel):
        trino_host: str = Field(default="trino", description="Trino hostname")
        trino_port: int = Field(default=8080, description="Trino port")
        trino_user: str = Field(default="trino", description="Trino user")
        trino_catalog: str = Field(default="delta", description="Trino catalog")
        trino_schema: str = Field(default="default", description="Trino schema")
        use_mock_data: bool = Field(default=True, description="Return canned data instead of querying Trino (for POC/demo)")
        max_context_rows: int = Field(default=5, description="Max sample rows to include in LLM context summary")

    def __init__(self):
        self.valves = self.Valves()
        self.mock_csv = "..."  # Embedded CSV string (see notes below)
```

#### Key design: Embedding the CSV data

Open WebUI functions are single Python files — no adjacent file access, no relative imports. The stub CSV data must be embedded directly in the Python file as a string constant.

```python
MOCK_DATA_CSV = """\
accession_number,epic_mrn,modality,...
ACC001,MRN001,CT,...
ACC002,MRN002,MR,...
...
"""
```

When you finalize the CSV content in `scout_query_stub_data.csv`, copy it into the tool's Python file as the `MOCK_DATA_CSV` constant. The separate CSV file exists for readability and version control; the Python file is what gets installed.

#### Tool method: `query`

The LLM-callable method. Its name, docstring, and parameter types are what the LLM uses to decide when to invoke it.

```python
async def query(
    self,
    sql: str,
    __user__: dict = {},
    __event_emitter__: Callable = None,
) -> str:
    """
    Execute a SQL query against the Scout Delta Lake database and return results.
    Use this tool whenever you need to look up radiology report data.
    The query should be valid Trino SQL against the 'reports' table.

    :param sql: The SQL query to execute
    :return: A summary of the query results
    """
```

#### Method implementation (mock mode)

```
1. Emit status: "Executing query..."
2. If use_mock_data is True:
   a. Parse MOCK_DATA_CSV into a data structure (pandas DataFrame or list of dicts)
   b. Apply rudimentary SQL-like filtering if feasible, or just return all rows
      (For the POC, returning the full mock dataset regardless of query is fine.
       The LLM will see the summary and work with it.)
3. If use_mock_data is False:
   a. [Future] Connect to Trino via trino-python-client and execute the SQL
   b. Fetch results into the same data structure
4. Generate CSV bytes from the results
5. Store as Open WebUI file:
   a. Import: from open_webui.models.files import Files, FileForm
   b. Import: from open_webui.storage.provider import Storage
   c. Generate a file_id (uuid4)
   d. Upload CSV bytes via Storage.upload_file()
   e. Create file record via Files.insert_new_file()
6. Emit file event to attach to the message:
   await __event_emitter__({
       "type": "files",
       "data": {"files": [{"type": "file", "id": file_id, ...}]}
   })
7. Emit status: "Query complete" (done=True)
8. Build and return summary string for LLM context:
   "Query returned {N} rows across {M} columns.
    Columns: {column_list}
    Sample ({max_context_rows} rows):
    {tabular_sample}
    Full results are saved as file '{filename}' and visible to the user."
```

The summary string is what enters the LLM context. The full data stays in the file.

#### Stretch: Rich UI data table

If time permits, return a tuple of `(HTMLResponse, summary_string)`:

```python
from fastapi.responses import HTMLResponse

html = f"""
<html>
<head>
    <style>
        table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
        th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; }}
        th {{ background: #f5f5f5; position: sticky; top: 0; }}
        tr:hover {{ background: #f9f9f9; }}
    </style>
</head>
<body>
    <div style="max-height: 400px; overflow: auto;">
        {html_table}
    </div>
    <p style="color: #666; font-size: 12px; margin-top: 8px;">
        Showing {N} rows. Full data available as attached CSV file.
    </p>
</body>
</html>
"""
return (
    HTMLResponse(content=html, headers={"Content-Disposition": "inline"}),
    summary_string
)
```

The iframe renders the table visually; the `summary_string` is what the LLM sees.

#### Further stretch: Rich UI charts

For charts, the tool could return a Vega-Lite spec rendered in an iframe. However, making charts that the user can tweak via conversation requires the LLM to understand how to modify the Vega-Lite spec, which is a substantial prompt engineering effort. Not recommended for this POC.

### Step 1.3: Update the system prompt

**File**: `ansible/roles/open-webui/files/gpt-oss-scout-query-prompt.md`

Changes needed:
- Replace all references to "Trino MCP" with "Scout Query Tool" (or whatever the tool method is named)
- Update the preamble: "You have access to **Scout Query Tool** for querying the Scout Delta Lake."
- Add a note about result handling: "Query results are saved as CSV files. The user can see the full data; you receive a summary. Reference the summary when discussing results, but do not attempt to reproduce the full dataset."
- The SQL patterns, examples, and troubleshooting sections can stay largely the same — the LLM still generates Trino SQL, the tool just handles it differently
- Remove or soften the "never fabricate data" rule since mock mode will always return the same data regardless of query. Perhaps: "In demo mode, query results are simulated. Treat the returned data as representative."

### Step 1.4: Install and configure

Manual steps (add to README):

1. Navigate to Admin Panel > Functions > Create New Function
2. Set Function ID: `scout_query_tool`
3. Paste contents of `scout_query_tool.py`
4. Save; configure Valves (trino connection details, mock mode toggle)
5. Edit the Scout Explorer model:
   - Remove Trino MCP from tools
   - Enable Scout Query Tool
   - Ensure Function Calling is set to "Native" (required for tool invocation)
6. Update the model's system prompt with the revised `gpt-oss-scout-query-prompt.md`
7. Test: Ask "Show me CT studies from 2024" — should see a file attachment and summary

---

## Phase 2: Send to XNAT Action

This is an Open WebUI Action function (class `Action`) that adds a "Send to XNAT" button to the message toolbar.

### Step 2.1: Prepare the XNAT logo icon

**File**: `ansible/roles/open-webui/files/xnat_logo_grey.svg`

The action button needs an icon via the `icon_url` field, which accepts HTTP/HTTPS URLs. Options for serving it:

- **Option A (simplest for POC)**: Use a data URI in the `icon_url` field. The docs discourage this due to "API payload bloat," but for a single small SVG it's negligible. Encode the greyscale SVG as a data URI: `data:image/svg+xml;base64,...`
- **Option B (production-ready)**: Serve the SVG from a static file server or embed it in the Open WebUI container. More work, better practice.

For the POC, go with Option A. The SVG should be:
- Greyscale to match Open WebUI's button aesthetic
- Small (< 2KB)
- Recognizable as the XNAT logo

Open WebUI's own action buttons (copy, regenerate, edit, etc.) are monochrome SVG icons, approximately 16-20px rendered size. Match that style.

Regarding light/dark mode: Open WebUI's built-in icons are single-color SVGs that work in both modes (they use `currentColor` or a neutral grey). For the POC, a medium grey (#888 or similar) SVG will be fine in both modes. A production version could use `currentColor` to adapt.

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
            default="https://xnat.example.org",
            description="XNAT instance base URL"
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

6. Build XNAT API payload (mock)
   - Construct a JSON object representing what would be sent:
     {
         "xnat_base_url": valves.xnat_base_url,
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

7. Show result (Step 3) via __event_call__ execute
   - Title: "XNAT Export — Demo Mode"
   - Show the constructed JSON payload in a formatted code block
   - Message: "In production, this payload would be sent to {xnat_base_url}/api/..."
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

### Step 2.4: Install and configure

Manual steps (add to README):

1. Navigate to Admin Panel > Functions > Create New Function
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
   - JSON payload is displayed showing the constructed request
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

### Test 6: Multiple files in conversation
1. Ask two separate queries to generate two file attachments
2. Click "Send to XNAT" on the message with both files (or on any message)
3. Verify: file selection dropdown appears, can choose which file to export

---

## Implementation Order

This is the recommended order for incremental implementation. Each step is independently testable.

| Step | Description | Depends On | Files |
|------|-------------|------------|-------|
| 1 | Create stub CSV data | — | `scout_query_stub_data.csv` |
| 2 | Create Scout Query Tool (mock mode, no Rich UI) | Step 1 | `scout_query_tool.py` |
| 3 | Update system prompt | — | `gpt-oss-scout-query-prompt.md` |
| 4 | Install tool, update model config, test query flow | Steps 2, 3 | README update |
| 5 | Prepare XNAT logo SVG | — | `xnat_logo_grey.svg` |
| 6 | Create XNAT Export Action (input form only) | Step 5 | `xnat_export_action.py` |
| 7 | Add confirmation dialog to action | Step 6 | `xnat_export_action.py` |
| 8 | Add mock API call and result display | Step 7 | `xnat_export_action.py` |
| 9 | Install action, test full flow | Steps 4, 8 | README update |
| 10 | [Stretch] Add Rich UI table to query tool | Step 4 | `scout_query_tool.py` |

---

## Open Questions and Risks

### Open Questions

1. **File event format**: The exact schema for the `files` event emitter is poorly documented. The approach outlined (using `Files.insert_new_file()` + emitting file IDs) is based on source code reading, not official docs. May need experimentation.

2. **pandas availability**: The Open WebUI container may or may not have pandas. If not and pip install is disabled, fall back to the `csv` standard library module. This is less ergonomic but sufficient.

3. **`__event_call__` timeout**: Default is 300 seconds per call. Complex confirmation dialogs (user reading through accession numbers) could hit this if the user walks away. The `WEBSOCKET_EVENT_CALLER_TIMEOUT` env var can increase this. Worth noting in the README.

4. **Action button on all messages**: Open WebUI shows action buttons on every assistant message, not just ones with file attachments. The action must gracefully handle being clicked on a message with no relevant files.

5. **File content access pattern**: The plan uses `Files.get_file_by_id()` + `Storage.get_file()` to read file contents. This imports Open WebUI internals, which could break on version updates. The REST API alternative (`/api/v1/files/{id}/content`) is more stable but requires auth token management.

### Risks

1. **Open WebUI version sensitivity**: Native Tool functions depend on internal APIs (`open_webui.models.files`, `open_webui.storage.provider`). These may change between Open WebUI versions. The Trino MCP tool avoided this by being an external service. Mitigation: pin Open WebUI version; test after upgrades.

2. **`execute` event browser compatibility**: The custom HTML/JS modal approach runs unsandboxed JS in the page context via `new Function()`. This is powerful but fragile — Open WebUI UI updates could break assumptions about DOM structure or CSS variables. Mitigation: use minimal, self-contained styles; don't depend on Open WebUI's internal DOM structure.

3. **Native function calling mode**: The existing Scout Explorer model uses "Native" function calling mode (required for MCP tools). Native mode may have quirks with `__event_emitter__` — the research noted that content emitted via `message`/`replace` events can flicker because the model's completion snapshot overwrites emitted content. File events and status events should be unaffected, but test carefully.

4. **CSP restrictions on Rich UI**: Scout's Traefik CSP middleware blocks external resources. Rich UI iframes should work (they use `self` origin), but any external library CDN links (e.g., DataTables.js) would be blocked. All Rich UI HTML/CSS/JS must be inline and self-contained.
