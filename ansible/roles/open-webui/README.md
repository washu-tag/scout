# Open WebUI Role

Deploys Open WebUI with Ollama for AI-powered chat interface in Scout.

## Overview

Open WebUI provides a user-friendly interface for interacting with language models via Ollama. In Scout, it's configured with:
- **Keycloak OAuth** for authentication and role-based access control
- **Trino MCP tool** for natural language querying of radiology reports in the Delta Lake
- **Redis** for distributed websocket coordination

## Deployment

```bash
cd ansible
make install-chat
```

The role automatically:
- Creates PostgreSQL database and Redis instance for Open WebUI
- Deploys Ollama and Open WebUI via Helm
- Pulls configured Ollama models
- Creates the Scout custom model `gpt-oss-120b-long:latest`

### Air-Gapped Deployment

In air-gapped environments (`air_gapped: true`), both model pulling and Scout model creation run on the staging node:

- Models are downloaded to shared NFS storage (`ollama_nfs_path`)
- The air-gapped cluster mounts this NFS read-only
- Scout custom model creation also runs on staging with NFS storage

**Required for air-gapped:**
- `ollama_nfs_path`: Shared NFS path accessible by both staging and cluster

**First install - manual model load required:**

After the initial `make install-chat`, the Scout model exists on NFS but is not loaded into memory on the air-gapped Ollama instance. The first user request will experience a slow cold start while the model loads.

To wait for the pull Job **on staging cluster**:
```bash
# Wait for the pull Job to complete
kubectl get jobs -n ollama -l app=ollama-pull-models -w
```

To pre-load the model after the pull Job completes (replace the model name with your `scout_model_name` if customized) **on Scout cluster**:
```bash
# Load the Scout model into memory (default: gpt-oss-120b-long:latest)
kubectl exec -n ollama deploy/ollama -- ollama run gpt-oss-120b-long:latest "hi"
```
Or, execute a chat in Open WebUI after you've configured the appropriate settings (see [Post-Deployment Configuration](#post-deployment-configuration)).

On subsequent Ollama pod restarts, the model loads automatically via a lifecycle hook.

### Required Configuration

See `defaults/main.yaml` for all available variables. Key requirements in `inventory.yaml`:

**Required Secrets** (use Ansible Vault):
- `open_webui_postgres_password`
- `open_webui_secret_key`
- `open_webui_redis_password`
- `keycloak_open_webui_client_secret`

**Optional Overrides:**
- `ollama_models`: List of additional models to pull
- `scout_model_create`: Set to `false` to skip Scout model creation (default: `true`)
- `ollama_storage_class` / `open_webui_storage_class`: Custom storage class (uses cluster default if not specified)
- `ollama_storage_size` / `open_webui_storage_size`: PVC storage sizes (defaults: 5Gi / 2Gi)
- Resource limits, etc.

See `inventory.example.yaml` for configuration examples

## Post-Deployment Configuration

After deploying via Ansible, configure Open WebUI through the web interface to complete the Scout Explorer setup.

### Prerequisites

- Open WebUI deployed and accessible
- Scout custom model `gpt-oss-120b-long:latest` created (automated by Ansible)
- Trino MCP server deployed (automatically deployed with Trino if `mcp_trino_enabled: true`)

### Configuration Steps

#### 1. Verify Scout Model (Automated)

The Scout custom model is automatically created by Ansible. You can verify it exists:

```bash
kubectl exec -n ollama deploy/ollama -- ollama list
```

You should see `gpt-oss-120b-long:latest` in the list.

**Note:** If you need to manually create or recreate the model:
```bash
kubectl exec -it -n ollama deploy/ollama -- sh
cat > Modelfile <<EOF
FROM gpt-oss:120b
PARAMETER num_predict -1
PARAMETER num_ctx 131072
PARAMETER num_keep 32768
EOF
ollama create gpt-oss-120b-long:latest -f Modelfile
exit
```

#### 2. Add Trino MCP Tool

Configure the Trino MCP external tool to enable SQL querying:

1. Navigate to **Admin Panel → Settings → External Tools** (requires admin access)
2. Click **+ (Add Server)**
3. Configure the tool:
   - **Type**: `MCP (Streamable HTTP)`
   - **ID**: `scout-db`
   - **Name**: `Trino MCP`
   - **Description**: `Query Scout Delta Lake with Trino`
   - **Server URL**: `http://mcp-trino.scout-analytics:8080/mcp`
     - Adjust namespace if Trino is deployed elsewhere: `http://mcp-trino.<namespace>:8080/mcp`
   - **Auth**: `None`
   - **Visibility**: `Public`
4. Click **Save**

#### 3. Add Knowledge in Open WebUI

1. Navigate to **Workspace (left sidebar) → Knowledge → New Knowledge**
2. Create a new knowledge base, tweaking name/description if desired:
   - **Name**: `Scout Capabilities`
   - **Description**: `Provides extra context to the model on information about the Scout database and how to interact with it`
   - **Visibility**: `Public`
3. Using **+ button → Upload files**, add documents to the collection:
   - `docs/source/dataschema.md`
   - (optional) `ansible/roles/open-webui/files/gpt-oss-charting.md`

#### 4. Configure Model in Open WebUI

1. Navigate to **Admin Panel → Settings → Documents** (requires admin access)
2. Replace the `RAG Template` with the contents of `ansible/roles/open-webui/files/rag-prompt.md` and save.
3. Load the "Models" tab and find `gpt-oss-120b-long:latest` in the model list
4. Optionally disable all other models
5. Click the **edit icon** (pencil) next to `gpt-oss-120b-long:latest`
6. Configure the following settings:
   - **Model Name**: `Scout Explorer`
   - **Description**: `Intelligent data exploration`
   - **Visibility**: `Public`
   - **System Prompt**: Copy contents of `ansible/roles/open-webui/files/gpt-oss-scout-query-prompt.md`
   - **Advanced Params**:
     - **Function calling**: `Native`
     - **Keep alive**: `-1` (keeps model loaded indefinitely)
     - **Reasoning Effort**: `high`
   - **Prompt Suggestions**: Select "Custom" and add sample prompts
   - **Knowledge**: Using "Select Knowledge" add `dataschema.md` and optionally `gpt-oss-charting.md`
   - **Tools**: Enable "Trino MCP", disable "Web Search" and "Code Interpreter"
7. Click **Save**

#### 5. Install Link Sanitizer Filter

Install a security filter to prevent data exfiltration via external links in LLM responses. This complements the CSP middleware (which blocks automatic resource loading) by also blocking clickable links. See [ADR 0010](../../../docs/internal/adr/0010-open-webui-link-exfiltration-filter.md) for details.

1. Navigate to **Admin Panel → Functions** (requires admin access)
2. Click **+ (New Function)**
3. Set Name to "Link Sanitizer Filter"
4. Set Description to "Removes external URLs from LLM responses to prevent data exfiltration."
5. Copy the contents of `ansible/roles/open-webui/files/link_sanitizer_filter.py` into the code editor and click **Save**
6. Click the **gear icon** next to the new function to configure Valves:
   - **internal_domains**: Your organization's domain (e.g., `example.com`)
     - This allows all subdomains: `scout.example.com`, `api.example.com`, etc.
   - **replacement_text**: Text shown in place of removed links (default is fine)
7. Enable the filter (you still have to add it to each model) AND/OR enable the filter globally:
   - Click the **"..." menu** next to the function
   - Toggle **Global** to enable for all models

**What the filter does:**
- Removes external URLs from LLM responses before display
- Preserves internal URLs matching your configured domain
- Handles both markdown links `[text](url)` and raw URLs
- Prevents HIPAA violations from PHI being transmitted via clicked links

#### 6. Install Context Summarization Filter

Install a filter to handle long conversations that approach the 128K context window limit. Without this filter, Ollama silently truncates older messages, causing conversations to "fall apart." See [ADR 0013](../../../docs/internal/adr/0013-open-webui-context-summarization-filter.md) for details.

1. Navigate to **Admin Panel → Functions** (requires admin access)
2. Click **+ (New Function)**
3. Set Name to "Context Summarization Filter"
4. Set Description to "Summarizes older conversation history when approaching context limits."
5. Copy the contents of `ansible/roles/open-webui/files/context_summarization_filter.py` into the code editor and click **Save**
6. Click the **gear icon** next to the new function to configure Valves:
   - **token_threshold**: `100000` (triggers at ~77% of 128K context)
   - **messages_to_keep**: `10` (recent messages to preserve intact)
   - **min_messages_to_keep**: `2` (minimum to keep when dynamically reducing)
   - **tool_result_token_threshold**: `500` (compact tool results exceeding this in old messages)
   - **ollama_url**: `http://ollama:11434` (default is correct for most deployments)
   - **summarizer_model**: Leave empty to use chat model, or specify a smaller/faster model
   - **debug_logging**: `true` (enable detailed logging for troubleshooting)
7. Enable the filter globally:
   - Click the **"..." menu** next to the function
   - Toggle **Global** to enable for all models

**What the filter does:**
- Detects when conversation approaches context limit (100K tokens by default)
- Shows status message: "Summarizing conversation (X tokens)..."
- Preserves base system prompt (Scout query instructions)
- Summarizes older user/assistant messages via Ollama API call
- Compacts old tool results to brief descriptions with sample data (e.g., "[Tool: 10 rows | {"diagnosis": "Malignant neoplasm...", "count": 5}]")
- Keeps recent messages intact for accurate context
- Lets RAG re-retrieve fresh knowledge per query
- Shows completion status: "Summarized: X → Y tokens"
- Falls back gracefully to truncation if summarization fails (API errors, timeouts)

**Note:** Summarization adds ~5-10 seconds of latency when triggered. The filter only activates when the token threshold is exceeded.

**Debugging:** When `debug_logging` is enabled, detailed logs are printed showing before/after message counts, token counts, and message previews. View logs with:
```bash
kubectl logs -n ollama deploy/open-webui -f | grep "\[ContextSummarization\]"
```

#### 7. Disable Arena Model

1. Navigate to **Admin Panel → Settings → Evaluations**
2. Disable Arena Model

#### 8. Verify Configuration

Test the configuration to ensure everything is working:

1. Start a new chat in Open WebUI
2. Select the **`Scout Explorer`** model from the model dropdown
3. Send a test query: `How many radiology reports are in the database?`
4. The model should:
   - Automatically use the Trino MCP tool to execute a SQL query
   - Return actual results from the Delta Lake
   - Display the tool usage in the chat interface (expandable section)

**Example Expected Behavior:**

```
User: How many reports are there?

Assistant (Scout Explorer): [Uses Trino MCP tool]

I'll query the database to get the total count of reports.

[Tool Call: trino_query_execute]
Query: SELECT COUNT(*) as total_reports FROM reports;

Result: 1,234,567 reports

There are 1,234,567 radiology reports in the Scout database.
```

If the tool is not working, check:
- Trino MCP service is running: `kubectl get svc -n scout-analytics mcp-trino`
- Tool configuration in Open WebUI Admin Settings
- Model has Function Calling set to "Native"

### Common Queries to Test

Once configured, try these example queries:

1. **Basic counts**: `How many CT reports are there?`
2. **Time-based**: `How many reports from 2024?`
3. **Filtered search**: `Find chest X-ray reports with pneumonia diagnosis`
4. **Demographics**: `What's the age distribution of patients in the database?`
5. **Complex analysis**: `Show me the top 5 most common modalities by report count`

## Troubleshooting

### Check Deployment Status

```bash
# Check pods
kubectl get pods -n ollama

# Check logs
kubectl logs -n ollama deploy/open-webui
kubectl logs -n ollama deploy/ollama

# Verify Scout model was created
kubectl get jobs -n ollama -l app=ollama-create-scout-model
kubectl exec -n ollama deploy/ollama -- ollama list
```

### Common Issues

**Scout model not created:**
- Check job logs: `kubectl logs -n ollama job/<job-name>`
- Verify base model was pulled: `kubectl exec -n ollama deploy/ollama -- ollama list`

**MCP tool not working:**
- Verify MCP server is running: `kubectl get pods -n scout-analytics -l app.kubernetes.io/name=mcp-trino`
- Test connectivity: `kubectl exec -n ollama deploy/open-webui -- curl http://mcp-trino.scout-analytics:8080/health`
- In Open WebUI model settings, ensure Function Calling is set to "Native"

**Authentication issues:**
- Users must have Keycloak roles: `open-webui-user` or `open-webui-admin`

## Related Documentation

- **Main Scout Docs**: https://washu-scout.readthedocs.io/
- **Open WebUI Docs**: https://docs.openwebui.com/
- **Scout Query Prompt**: `files/gpt-oss-scout-query-prompt.md`
- **Link Sanitizer Filter**: `files/link_sanitizer_filter.py`
- **Context Summarization Filter**: `files/context_summarization_filter.py`
- **ADRs**:
  - [ADR 0009: Content Security Policy](../../../docs/internal/adr/0009-open-webui-content-security-policy.md)
  - [ADR 0010: Link Exfiltration Filter](../../../docs/internal/adr/0010-open-webui-link-exfiltration-filter.md)
  - [ADR 0013: Context Summarization Filter](../../../docs/internal/adr/0013-open-webui-context-summarization-filter.md)
