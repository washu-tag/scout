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
- Creates each entry in `scout_models` as a Modelfile-derived variant with extended-context parameters baked in (default: `gpt-oss-120b-long:latest` from `gpt-oss:120b`)

### Air-Gapped Deployment

In air-gapped environments (`air_gapped: true`), both model pulling and Scout model creation run on the staging node:

- Models are downloaded to shared NFS storage (`ollama_nfs_path`)
- The air-gapped cluster mounts this NFS read-only
- Scout custom model creation also runs on staging with NFS storage

**Required for air-gapped:**
- `ollama_nfs_path`: Shared NFS path accessible by both staging and cluster

**First install - manual model load required:**

After the initial `make install-chat`, Scout models exist on NFS but are not loaded into memory on the air-gapped Ollama instance. The first user request to each model experiences a slow cold start.

To wait for the pull Job **on staging cluster**:
```bash
# Wait for the pull Job to complete
kubectl get jobs -n ollama -l app=ollama-pull-models -w
```

To pre-load each Scout model after the pull Job completes **on Scout cluster**:
```bash
# Load each Scout model into memory (use the names from your scout_models list)
kubectl exec -n ollama deploy/ollama -- ollama run gpt-oss-120b-long:latest "hi"
```
Or, execute a chat in Open WebUI after you've configured the appropriate settings (see [Post-Deployment Configuration](#post-deployment-configuration)).

On subsequent Ollama pod restarts, models with `preload: true` (default) load automatically via the lifecycle hook. Models with `preload: false` cold-load on first request.

### Required Configuration

See `defaults/main.yaml` for all available variables. Key requirements in `inventory.yaml`:

**Required Secrets** (use Ansible Vault):
- `open_webui_postgres_password`
- `open_webui_secret_key`
- `open_webui_redis_password`
- `keycloak_open_webui_client_secret`

**Optional Overrides:**
- `scout_models`: List of derived Scout model variants (each with `base`, `name`, `num_ctx`, `num_keep`, `num_predict`, `preload`). Bases are pulled automatically. See `defaults/main.yaml` for schema.
- `ollama_models`: List of additional models to pull (beyond `scout_models` bases)
- `scout_model_create`: Set to `false` to skip Scout model creation entirely (default: `true`)
- `ollama_storage_class` / `open_webui_storage_class`: Custom storage class (uses cluster default if not specified)
- `ollama_storage_size` / `open_webui_storage_size`: PVC storage sizes (defaults: 5Gi / 2Gi)
- Resource limits, etc.

**Multi-model VRAM management:**

Ollama's runtime default `num_ctx` is 4096 regardless of the model's native max — that's why each Scout model bakes its `num_ctx` into a Modelfile-derived variant. With multiple `scout_models`, total resident weights + KV cache may exceed GPU memory if all models are kept hot. Use `preload: false` for models that should cold-load on demand, and configure per-model **Keep Alive** in Open WebUI (Admin → Models → Advanced Params): `-1` for resident models, a finite value (e.g., `5m`) for cold-load models so they unload when idle and free VRAM for the resident set.

See `inventory.example.yaml` for configuration examples

## Post-Deployment Configuration

After deploying via Ansible, configure Open WebUI through the web interface to complete the Scout Explorer setup.

### Prerequisites

- Open WebUI deployed and accessible
- Scout custom models from `scout_models` created (automated by Ansible)
- Trino MCP server deployed (automatically deployed with Trino if `mcp_trino_enabled: true`)

### Configuration Steps

#### 1. Verify Scout Models (Automated)

Scout custom models are automatically created by Ansible. You can verify they exist:

```bash
kubectl exec -n ollama deploy/ollama -- ollama list
```

You should see each entry from `scout_models` (default: `gpt-oss-120b-long:latest`) in the list.

**Note:** If you need to manually create or recreate a model, substitute the values from your `scout_models` entry:
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

#### 2. Add Trino MCP Tool — automated

Registered automatically via the `TOOL_SERVER_CONNECTIONS` env var (see `tool_server_connections` in `defaults/main.yaml`). To override the URL, set `mcp_trino_url` in inventory; to skip registration, set `tool_server_connections: []`.

Verify after deploy:

```bash
kubectl exec -n {{ chatbot_namespace }} deploy/open-webui -- \
  curl -s http://localhost:8080/api/v1/configs/tool_servers
```

**Note:** Open WebUI stores tool config as PersistentConfig — env vars seed initial values on first launch. To re-seed an existing deployment from updated env values, drop the corresponding row from the OWUI `config` table or wipe the persistence PVC.

#### 3. Add Knowledge in Open WebUI — automated

Knowledge collections from `open_webui_knowledge_collections` (defaults/main.yaml) are created on every deploy. For each file: kubectl-cp into the OWUI pod → POST /api/v1/files/ multipart → poll /process/status until completed → POST /api/v1/knowledge/{id}/file/add. The full collection object is then attached to every Scout model's `meta.knowledge` so RAG retrieval activates automatically.

To customize per environment:
- Override `open_webui_knowledge_collections` to add files or new collections
- A single source-of-truth: any model with a `ui:` block in `scout_models` gets every collection attached

Verify after deploy:
```bash
kubectl exec -n {{ chatbot_namespace }} deploy/open-webui -- \
  curl -s http://localhost:8080/api/v1/knowledge/ \
  -H "Authorization: Bearer <admin-jwt>" | jq '.items[].name'
```

#### 4. Configure Model in Open WebUI

The RAG Template is now seeded automatically from `files/rag-prompt.md` via the `RAG_TEMPLATE` env var; steps below cover only the model-specific config that still requires the admin UI (Phase 2 of the automation roadmap).

1. ~~Navigate to **Admin Panel → Settings → Documents** and replace the `RAG Template`~~ — automated via the `RAG_TEMPLATE` env var. Override `open_webui_rag_template_file` to point at a different bundled file.
2. Load the **Models** tab and find your Scout model (e.g., `gpt-oss-120b-long:latest`) in the list
3. Optionally disable all other models
4. Click the **edit icon** (pencil) next to your Scout model
5. Configure the following settings:
   - **Model Name**: `Scout Explorer`
   - **Description**: `Intelligent data exploration`
   - **Visibility**: `Public`
   - **System Prompt**: Copy contents of `ansible/roles/open-webui/files/gpt-oss-scout-query-prompt.md`
   - **Advanced Params**:
     - **Function calling**: `Native`
     - **Keep alive**: `-1` for resident models (gemma4-31b-long, qwen3.6-long); `5m` for cold-load models (gpt-oss-120b-long) so they unload when idle and free VRAM
     - **Reasoning Effort**: `high`
   - **Prompt Suggestions**: Select "Custom" and add sample prompts
   - **Knowledge**: Using "Select Knowledge" add `dataschema.md` and optionally `gpt-oss-charting.md`
   - **Tools**: Enable "Trino MCP", disable "Web Search" and "Code Interpreter"
6. Click **Save**

Repeat for each model in your `scout_models` list.

#### 5 & 6. Install Filter Functions — automated

Both the Link Sanitizer ([ADR 0010](../../../docs/internal/adr/0010-open-webui-link-exfiltration-filter.md)) and Context Summarization ([ADR 0014](../../../docs/internal/adr/0014-open-webui-context-summarization-filter.md)) filters are created/updated, configured with valves, and toggled global on every deploy. Source list: `open_webui_filter_functions` in `defaults/main.yaml`.

To customize valves per environment (e.g., Link Sanitizer's `internal_domains`), override in inventory:
```yaml
open_webui_link_sanitizer_internal_domains: 'example.com'
```

To skip a filter, override the list to omit it. To disable Phase 2 entirely, set `open_webui_admin_setup_enabled: false`.

Verify after deploy:
```bash
kubectl exec -n {{ chatbot_namespace }} deploy/open-webui -- \
  curl -s http://localhost:8080/api/v1/functions/ \
  -H "Authorization: Bearer <admin-jwt>" | jq '.[].id'
```

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

#### 7. Disable Arena Model — automated

Disabled automatically via `ENABLE_EVALUATION_ARENA_MODELS=false` (set from `open_webui_enable_arena_models` in `defaults/main.yaml`). To re-enable, override to `true` in inventory.

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
  - [ADR 0014: Context Summarization Filter](../../../docs/internal/adr/0014-open-webui-context-summarization-filter.md)
