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

1. Navigate to **[User Icon in bottom left]  → Admin Panel → Settings → External Tools** (requires admin access)
2. Click **+ (Add Server)**
3. Configure the tool with the following settings:
   - **Type**: `MCP (Streamable HTTP)`
   - **ID**: `scout-db`
   - **Name**: `Trino MCP`
   - **Description**: `Query Scout Delta Lake with Trino`
   - **Server URL**: `http://mcp-trino.trino:8080/mcp`
      - **Note**: Adjust namespace if Trino is deployed in a different namespace
      - Format: `http://mcp-trino.<trino_namespace>:8080/mcp`
   - **Auth**: `None`
4. Click **Save**

#### 3. Configure Model in Open WebUI

Access the Open WebUI interface and configure the model settings:

1. Navigate to **[User Icon in bottom left]  → Admin Panel → Settings → Models** (requires admin access)
2. Find `gpt-oss-120b-long:latest` in the model list
3. (optionally) Disable all other models 
4. Click the **edit icon** (pencil) next to `gpt-oss-120b-long:latest`
5. Configure the following settings:

**Basic Settings:**
- **Model Name**: `Scout Explorer`
- **Description**: `Intelligent data exploration`

**Model Badge (Icon):**
- Upload or select an icon representing Scout (optional)
- Recommended: Upload the Scout logo if available

**Visbility:**
- Set to Public

**Model System Prompt:**
- Navigate to **System Prompt** section
- Copy the entire contents of `ansible/roles/open-webui/files/gpt-oss-scout-query-prompt.md`
- Paste into the system prompt field
- The prompt instructs the model to use the Trino MCP tool for querying the Delta Lake

**Advanced Parameters:**
- Navigate to **Advanced Params** section
- **Function calling**: Set to **`Native`**
- **Keep alive**: Set to **`-1`** (keeps model loaded indefinitely)

**Prompt Suggestions:**
- Navigate to **Prompt suggestions** section
- Select "Custom"
- Add some sample prompts

**Tools:**
- Check "Trino MCP"
- Uncheck "Web Search"

6. Click **Save** to apply changes

#### 4. Verify Configuration

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
- Trino MCP service is running: `kubectl get svc -n trino mcp-trino`
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
- Verify MCP server is running: `kubectl get pods -n trino -l app.kubernetes.io/name=mcp-trino`
- Test connectivity: `kubectl exec -n ollama deploy/open-webui -- curl http://mcp-trino.trino:8080/health`
- In Open WebUI model settings, ensure Function Calling is set to "Native"

**Authentication issues:**
- Users must have Keycloak roles: `open-webui-user` or `open-webui-admin`

## Related Documentation

- **Main Scout Docs**: https://washu-scout.readthedocs.io/
- **Open WebUI Docs**: https://docs.openwebui.com/
- **Scout Query Prompt**: `files/gpt-oss-scout-query-prompt.md`
