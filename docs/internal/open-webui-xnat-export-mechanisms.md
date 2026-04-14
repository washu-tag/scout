# Open WebUI Extension Mechanisms for XNAT Data Export

## Context

We want to enable users of Scout's Open WebUI Chat interface to export query result data to an XNAT instance. The user's typical workflow is: chat with the LLM, which generates and executes SQL queries against Scout's Delta Lake via the Trino MCP tool, then export some or all of those results to XNAT.

This document surveys Open WebUI's extension mechanisms that could support this workflow, evaluates trade-offs, and recommends an approach. All mechanisms discussed here work without modifying Open WebUI source code.

## Open WebUI Extension System Overview

Open WebUI provides four types of server-side Python plugins, collectively called **Functions**:

| Type | Purpose | Trigger | Can Add UI? |
|------|---------|---------|-------------|
| **Tool** | Methods the LLM can call | LLM decides based on docstring/params | Rich UI iframes |
| **Filter** | Middleware on message flow | Automatic on every message | No (modifies data only) |
| **Action** | Buttons on chat messages | User clicks button | Rich UI iframes, dialogs |
| **Pipe** | Custom "model" endpoints | User selects as model | Rich UI iframes |

There is also a separate **Pipelines** framework that runs as its own container, but it is heavier to deploy and largely superseded by native Functions for our use case.

### Relevant Existing Scout Extensions

Scout already uses two filters (`link_sanitizer_filter.py`, `context_summarization_filter.py`) and a Trino MCP tool, demonstrating that the extension system is well-proven in our deployment.

## Mechanism 1: MCP Tool (Current Trino Pattern)

**How it works**: The LLM decides to call the tool based on the user's natural language request. The tool executes server-side and returns a string result into the LLM context.

**For XNAT export**: Define an MCP tool (or native Open WebUI Tool) with a method like `export_to_xnat(study_uids, project_id, ...)`. The user says "export these results to XNAT" and the LLM calls the tool.

**Pros**:
- Familiar pattern; already deployed for Trino queries
- LLM can prepare/transform data before calling the tool
- Natural language interface ("export the CT studies from last month to XNAT project FOO")

**Cons**:
- Unreliable trigger: the LLM decides *if and when* to call the tool; prompt engineering helps but doesn't guarantee it
- Data passes through LLM context, which can mangle it (numbers rounded, rows dropped, hallucinated values)
- Large result sets flood the context window
- User has no direct control over exactly what data is sent

## Mechanism 2: Action Function (Recommended for XNAT Export)

**How it works**: An Action registers one or more buttons that appear in the message toolbar beneath each assistant message. Clicking a button invokes a server-side Python `action()` method with full access to the conversation state and event system.

```python
class Action:
    class Valves(BaseModel):
        xnat_base_url: str = Field(default="", description="XNAT instance URL")
        xnat_project: str = Field(default="", description="Default XNAT project ID")

    actions = [
        {"id": "export_xnat", "name": "Export to XNAT", "icon_url": "..."},
    ]

    async def action(self, body: dict, __event_emitter__=None, __event_call__=None, __id__=None, **kwargs):
        # 1. Extract query results from the message/conversation
        # 2. Prompt user for confirmation
        confirmed = await __event_call__({
            "type": "confirmation",
            "data": {"title": "Export to XNAT", "message": f"Send {row_count} rows to XNAT project {project}?"}
        })
        if not confirmed:
            return
        # 3. Show progress
        await __event_emitter__({"type": "status", "data": {"description": "Exporting to XNAT...", "done": False}})
        # 4. Make XNAT API call
        result = await send_to_xnat(data)
        # 5. Notify user
        await __event_emitter__({"type": "status", "data": {"description": "Export complete", "done": True}})
```

**Pros**:
- Deterministic trigger: user clicks a button, no LLM interpretation needed
- Confirmation dialog before sending data
- Progress indicators via status events
- Toast notifications for success/failure
- Does not add anything to the LLM context
- Can be enabled globally or per-model
- Multiple buttons from a single Action function (export to XNAT, download CSV, etc.)

**Cons**:
- The action receives the *message content* (rendered text), not structured query results. It would need to parse data from the message or retrieve it from a stored location.
- Button appears on every assistant message (no conditional visibility)
- No custom modal UI beyond confirmation/input dialogs (richer interaction requires Rich UI iframes)

**Key detail**: Actions can use `__event_call__` for two interaction types:
- `confirmation`: OK/Cancel dialog, returns boolean
- `input`: Text input dialog, returns string (useful for "which XNAT project?")

## Mechanism 3: Tool + Action Hybrid (Recommended Overall Approach)

Combining a Tool and an Action addresses the weaknesses of each:

1. **Tool** handles the query execution, stores structured results server-side, and returns only a summary to the LLM
2. **Action** button lets the user trigger the XNAT export on the stored results

### Data Flow

```
User asks question
    -> LLM generates SQL
    -> Trino Tool executes query
    -> Tool stores full results server-side (temp file, MinIO, or in-memory cache)
    -> Tool returns summary to LLM ("5,432 rows, columns: [...], sample: [...]")
    -> Tool optionally renders Rich UI data table in iframe
    -> User sees results, clicks "Export to XNAT" button
    -> Action retrieves stored results (not from LLM context)
    -> Action prompts for confirmation
    -> Action calls XNAT API with full, accurate data
    -> Action shows success notification
```

This pattern keeps large data out of the LLM context while giving the user accurate data export.

## Mechanism 4: Filter Functions

Filters intercept messages at three stages:

- **`inlet()`**: Before the user message reaches the LLM. Could inject system instructions ("always offer XNAT export after queries") but cannot reliably trigger actions.
- **`stream()`**: During streaming response. Could monitor for query results in real-time.
- **`outlet()`**: After the complete response. Could post-process results.

**For XNAT export**: Filters alone are insufficient for triggering an export (no user interaction, no buttons). However, a filter could complement other mechanisms:
- An `inlet` filter could inject context about XNAT export availability
- An `outlet` filter could detect query results and tag messages for easy retrieval by an Action

**Important limitation**: `inlet()` is NOT re-triggered when tool results are sent back to the model for the continuation turn. This is a known limitation ([open-webui/open-webui#15225](https://github.com/open-webui/open-webui/issues/15225)). Filters cannot currently intercept tool results before they enter the LLM context on the tool-result-to-model round-trip.

## Mechanism 5: Pipe Functions (Custom Model)

A Pipe registers as a custom "model" in the chat sidebar. It handles the entire request/response cycle, giving full control over data flow.

**For XNAT export**: Overkill. A Pipe would replace the entire LLM interaction, requiring reimplementation of tool calling, streaming, etc. Not appropriate for adding export functionality to the existing Scout Explorer model.

## Mechanism 6: OpenAPI Tool Servers

Deploy a separate FastAPI service that Open WebUI calls as a tool server. The service handles XNAT API calls and data transformations independently.

**Pros**: Full control over dependencies, runs in its own container, scales independently.

**Cons**: No interactive events (no confirmation dialogs, no streaming status). Only returns a complete response. Requires deploying and managing an additional service.

**Verdict**: Good for heavy data processing, but the lack of interactive UI makes it less suitable as the primary export mechanism. Could serve as the backend that an Action or Tool calls.

## Mechanism 7: Rich UI Embedding

Tools and Actions can return `HTMLResponse` with `Content-Disposition: inline` to render interactive iframes in the chat. These iframes can contain full HTML/CSS/JavaScript, including:
- Interactive data tables (AG Grid, DataTables.js)
- Download buttons
- Forms that submit prompts back to the chat via `parent.postMessage({type: 'input:prompt:submit', text: '...'})`
- External library loading

**For XNAT export**: A Rich UI table showing query results with an "Export to XNAT" button embedded in the iframe. The button could either trigger a postMessage back to the chat (which invokes the LLM again) or call a backend API endpoint directly.

**Consideration**: Rich UI iframes are sandboxed. Direct API calls from the iframe to XNAT would be subject to CORS and CSP restrictions (Scout's CSP middleware blocks external connections by default). Server-side export via an Action or Tool is more reliable.

## Addressing the Data Volume Problem

### The Problem

When the Trino MCP tool returns query results, the entire result set enters the LLM context. This:
- Wastes context window capacity on raw numbers
- Risks the LLM hallucinating or rounding values
- Degrades conversation quality as context fills up
- Makes data unreliable for downstream use (export to XNAT)

### Approaches to Solve This

#### A. Tool-Side Result Capping with Side-Channel Storage (Recommended)

Modify or wrap the Trino query tool to:
1. Execute the query and store full results in a temporary location (MinIO object, temp file on the Open WebUI pod, or a shared cache)
2. Return only a summary to the LLM:
   ```
   Query returned 5,432 rows across 12 columns.
   Columns: study_instance_uid, modality, message_dt, ...
   Sample (first 3 rows): [...]
   Full results stored as reference ID: abc-123
   ```
3. Optionally emit a Rich UI iframe showing the full data table
4. Optionally emit a file attachment (CSV) via the `files` event

The reference ID enables the Action to retrieve exact, untouched data for XNAT export.

**Note**: This requires either replacing the current Trino MCP tool with a native Open WebUI Tool function (which has access to `__event_emitter__` and can store data), or deploying a wrapper service. MCP tools alone cannot emit events or store side-channel data — they only return strings.

#### B. Filter-Based Context Compaction (Already Implemented)

Scout's `context_summarization_filter.py` already compacts large tool results in old messages when the context approaches 128K tokens. This is a safety net, not a primary solution — it only kicks in when the context is already bloated.

#### C. Instruction-Based Truncation

Modify the Scout Explorer system prompt to instruct the LLM to summarize large result sets rather than reproducing them verbatim. This is unreliable — LLMs often reproduce data anyway, especially when the tool result is in context.

#### D. For the POC: Mock Data Stub

For the immediate POC, the simplest approach:
1. Create an Open WebUI Tool function with a method like `get_export_data()` that returns a hardcoded mock dataset
2. The LLM calls this tool when the user asks to export
3. The tool returns mock data in a known schema
4. An Action button (or the tool itself) sends this data to the XNAT API

This avoids the data volume problem entirely during POC development and lets you focus on the XNAT API integration.

## Recommendations

### For the POC

1. **Create an Action function** with an "Export to XNAT" button. This provides a deterministic, user-controlled trigger for the export. Use `__event_call__` for confirmation and `__event_emitter__` for progress/status.

2. **Create a Tool function** with a mock `get_study_data()` method that returns a fixed dataset. This stubs out the data retrieval problem and gives the Action something concrete to export.

3. **Use Valves** for XNAT configuration (base URL, project ID, credentials or auth token). Admin-configurable via the Open WebUI admin panel.

4. **Don't use MCP for the XNAT export** — native Open WebUI Tools have access to the event system (confirmation dialogs, progress indicators) that MCP tools lack.

### For Production

1. **Replace or wrap the Trino MCP tool** with a native Open WebUI Tool that implements side-channel storage. Query results get stored server-side; only summaries enter the LLM context. This solves both the context flooding problem and provides reliable data for export.

2. **Keep the Action button** for user-triggered export. It retrieves stored results by reference ID and sends them to XNAT.

3. **Consider Rich UI** for rendering query results as interactive tables in iframes. This gives users a visual preview of what they're exporting without polluting the LLM context.

4. **Deploy an OpenAPI Tool Server** if XNAT integration requires heavy dependencies or processing that shouldn't run on the Open WebUI pod.

### Architecture Sketch

```
                          Open WebUI
                    ┌─────────────────────┐
                    │                     │
  User Chat ───────┤  Scout Explorer LLM  │
                    │        │            │
                    │   Tool: query_trino │──── Trino ──── Delta Lake
                    │        │            │
                    │   (stores results   │
                    │    server-side)      │
                    │        │            │
                    │   Action: Export     │──── XNAT REST API
                    │   to XNAT [button]  │
                    │        │            │
                    │   (retrieves stored  │
                    │    results, sends    │
                    │    to XNAT)          │
                    └─────────────────────┘
```

## References

- [Open WebUI Functions (Plugins)](https://docs.openwebui.com/features/extensibility/plugin/)
- [Action Functions](https://docs.openwebui.com/features/extensibility/plugin/functions/action/)
- [Tool Development](https://docs.openwebui.com/features/extensibility/plugin/tools/development/)
- [Rich UI Embedding](https://docs.openwebui.com/features/extensibility/plugin/development/rich-ui/)
- [Events Reference](https://docs.openwebui.com/features/extensibility/plugin/development/events/)
- [Filter Functions](https://docs.openwebui.com/features/extensibility/plugin/functions/filter/)
- [OpenAPI Tool Servers](https://docs.openwebui.com/features/extensibility/plugin/tools/openapi-servers/)
- [Pipelines Framework](https://docs.openwebui.com/features/extensibility/pipelines/)
- [Filter inlet not re-triggered on tool calls (Issue #15225)](https://github.com/open-webui/open-webui/issues/15225)
