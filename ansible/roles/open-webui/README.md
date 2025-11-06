# Open WebUI Role

Deploys Open WebUI with Ollama for AI-powered chat interface in Scout.

## Overview

Open WebUI provides a user-friendly interface for interacting with language models via Ollama. In Scout, it's configured with the Trino MCP (Model Context Protocol) tool to enable natural language querying of radiology reports in the Delta Lake.

## Deployment

The role automatically:
- Deploys Ollama and Open WebUI via Helm
- Creates necessary PostgreSQL database
- Ensures Scout base model is in `ollama_models` list (if `scout_model_create: true`)
- Pulls configured Ollama models (if `ollama_models` is defined)
- Creates the Scout custom model `gpt-oss-120b-long:latest` (if `scout_model_create: true`)

### Configuration Variables

Key variables in `defaults/main.yaml` (override in `inventory.yaml`):

**Storage:**
- `ollama_storage_size`: Storage size for Ollama models (default: `5Gi`)
- `open_webui_storage_size`: Storage size for Open WebUI data (default: `2Gi`)

**Models:**
- `ollama_models`: List of models to pull (e.g., `['gpt-oss:120b']`)
- `scout_model_create`: Enable/disable Scout custom model creation (default: `true`)
- `scout_base_model`: Base model for Scout (default: `gpt-oss:120b`)
- `scout_model_name`: Custom model name (default: `gpt-oss-120b-long:latest`)
- `scout_model_num_ctx`: Context window size (default: `131072` = 128K)
- `scout_model_num_keep`: Tokens to keep in memory (default: `32768`)
- `scout_model_num_predict`: Max tokens to generate (default: `-1` = unlimited)

**Resources:**
- `ollama_resources`: CPU/memory for Ollama pods
- `open_webui_resources`: CPU/memory for Open WebUI pods

**Required Secrets (must be set in `inventory.yaml`):**
- `open_webui_postgres_password`: PostgreSQL password (use Ansible Vault)
- `open_webui_secret_key`: Session management secret (use Ansible Vault)

### Example Inventory Configuration

```yaml
# inventory.yaml
all:
  vars:
    # Scout model will be created automatically
    # The base model (gpt-oss:120b) will be added to ollama_models automatically
    scout_model_create: true

    # Optional: Add additional models to pull
    # ollama_models:
    #   - llama3:70b

    # Required secrets (use ansible-vault)
    open_webui_postgres_password: !vault |
          $ANSIBLE_VAULT;1.1;AES256
          ...
    open_webui_secret_key: !vault |
          $ANSIBLE_VAULT;1.1;AES256
          ...
```

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

### Scout Model Not Created

If the Scout custom model wasn't created automatically:

1. Check if base model exists:
   ```bash
   kubectl exec -n ollama deploy/ollama -- ollama list | grep gpt-oss:120b
   ```

2. Check Job status:
   ```bash
   kubectl get jobs -n ollama -l app=ollama-create-scout-model
   kubectl logs -n ollama job/<job-name>
   ```

3. Manually create if needed (see step 1 above)

### Trino MCP Tool Not Working

1. Verify MCP server is deployed:
   ```bash
   kubectl get pods -n trino -l app.kubernetes.io/name=mcp-trino
   ```

2. Check MCP server logs:
   ```bash
   kubectl logs -n trino deploy/mcp-trino
   ```

3. Test connectivity from Open WebUI pod:
   ```bash
   kubectl exec -n ollama deploy/open-webui -- curl http://mcp-trino.trino:8080/health
   ```

4. Verify Trino MCP configuration in `ansible/roles/trino/defaults/main.yaml`:
   - `mcp_trino_enabled: true`
   - Correct Trino connection settings

### Model Not Using MCP Tool

1. Verify Function Calling is set to "Native" (not "Tools" or "Default")
2. Check that system prompt was correctly pasted
3. Try restarting the chat session
4. Verify the MCP tool is added in Admin Settings → External Tools

### Authentication Issues

Open WebUI requires initial admin user creation on first access. If you can't access admin settings:

1. Access Open WebUI for the first time
2. Create an admin account through the signup flow
3. The first user becomes the admin automatically
4. After logging in, you'll have access to Admin Settings

## Advanced Configuration

### Customizing the Model

To adjust model parameters, edit `inventory.yaml`:

```yaml
all:
  vars:
    scout_model_num_ctx: 65536  # Reduce context window if needed
    scout_model_num_keep: 16384  # Reduce keep tokens to save memory
```

Then redeploy:
```bash
cd ansible
make install-chat
```

### Disabling Scout Model Creation

If you want to use a different model or configure it manually:

```yaml
all:
  vars:
    scout_model_create: false
```

### Using Different Base Models

To use a different base model:

```yaml
all:
  vars:
    scout_base_model: llama3:70b
    ollama_models:
      - llama3:70b
```

## Architecture Notes

### Automatic Base Model Handling

When `scout_model_create: true`, the role automatically ensures the Scout base model (`gpt-oss:120b` by default) is added to the `ollama_models` list if it's not already present. This means:

- You don't need to manually add `gpt-oss:120b` to `ollama_models`
- The base model is pulled before the custom model is created
- If you override `scout_base_model`, that model will be automatically added instead
- Existing models in `ollama_models` are preserved (uses Ansible's `union` filter)

### Why Separate Model Creation?

The Scout custom model creation runs as a Kubernetes Job (similar to `pull_models.yaml`) because:

1. **Asynchronous execution**: Model creation can take time and shouldn't block deployment
2. **Idempotency**: Job only runs if model doesn't already exist
3. **Observability**: Job logs show creation progress and can be inspected
4. **Resilience**: Job retries on failure (up to 3 times)

### MCP Integration

The Trino MCP server provides a streamable HTTP interface that Open WebUI can consume directly. The architecture:

```
User Query → Open WebUI → Scout Explorer Model
                              ↓ (Function Call)
                         Trino MCP Server
                              ↓ (SQL Query)
                            Trino
                              ↓
                         Delta Lake
```

This allows the LLM to:
- Translate natural language to SQL
- Execute queries against the Delta Lake
- Return structured results to the user
- Handle complex multi-step queries

## Security Considerations

1. **Secrets Management**: Always use Ansible Vault for:
   - `open_webui_postgres_password`
   - `open_webui_secret_key`

2. **MCP Access**: The Trino MCP server is configured read-only (`allowWriteQueries: false`)

3. **Network Isolation**: All services communicate within the Kubernetes cluster using ClusterIP services

4. **Admin Access**: Only users with admin privileges can configure MCP tools and system settings

## Related Documentation

- **Main Scout Docs**: https://washu-scout.readthedocs.io/
- **Open WebUI Docs**: https://docs.openwebui.com/
- **MCP Protocol**: https://docs.openwebui.com/features/mcp/
- **Trino MCP Server**: Deployed from `ansible/roles/trino/tasks/mcp.yaml`
- **Query Prompt**: `ansible/roles/open-webui/files/gpt-oss-scout-query-prompt.md`

## Files in This Role

```
ansible/roles/open-webui/
├── README.md                           # This file
├── defaults/main.yaml                  # Default configuration variables
├── files/
│   └── gpt-oss-scout-query-prompt.md   # System prompt for Scout Explorer model
├── tasks/
│   ├── main.yaml                       # Entry point
│   ├── deploy.yaml                     # Main deployment logic
│   ├── pull_models.yaml                # Ollama model pulling (async)
│   ├── create_scout_model.yaml         # Scout custom model creation (async)
│   └── storage.yaml                    # PV/PVC setup
├── templates/
│   └── values.yaml.j2                  # Helm values template
└── meta/
    └── main.yaml                       # Role metadata
```
