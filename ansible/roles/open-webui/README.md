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
- Creates each entry in `scout_models` as a Modelfile-derived variant with extended-context parameters baked in. Default ships three: `gemma4-31b-long` (preload, default + task model), `qwen3.6-long` (cold-load, thinking-mode), and `gpt-oss-120b-long` (cold-load, large/cohort-building)
- Mints an admin JWT via the in-cluster Service and uses it to seed filter functions, custom Scout Explorer models, and other OWUI resources via the REST API on every deploy (Phase 2 — see [Bootstrap & Migration](#bootstrap--migration) and [Post-Deployment Configuration](#post-deployment-configuration))

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
kubectl get jobs -n scout-analytics -l app=ollama-pull-models -w
```

To pre-load each Scout model after the pull Job completes **on Scout cluster**:
```bash
# Load each Scout model into memory (use the names from your scout_models list)
kubectl exec -n scout-analytics deploy/ollama -- ollama run gemma4-31b-long:latest "hi"
kubectl exec -n scout-analytics deploy/ollama -- ollama run qwen3.6-long:latest "hi"
kubectl exec -n scout-analytics deploy/ollama -- ollama run gpt-oss-120b-long:latest "hi"
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

## Bootstrap & Migration

All Phase 2 work (admin user, filter functions, custom models, PersistentConfig re-push) runs as a Kubernetes Job deployed via a separate Helm chart at [`helm/open-webui-bootstrap/`](../../../helm/open-webui-bootstrap/). The Job is hooked as `helm.sh/hook: post-install,post-upgrade` so it runs after the OWUI chart finishes installing/upgrading, and re-runs on every `make install-chat` (its manifest carries a `checksum/config` annotation over the rendered ConfigMap).

The bootstrap user is `scout-deploy@scout-deploy.local` (configurable via `open_webui_bootstrap_email`).

### How it works

- **The Job uses OWUI's own container image** so the password-migration step can `import open_webui.internal.db` / `open_webui.models.auths` (same modules the app uses), and bcrypt versions match.
- **Password is derived from `open_webui_secret_key`** (SHA256 hex) inside the script. No separate secret to track; rotating the secret-key auto-rotates the bootstrap login.
- **`ENABLE_INITIAL_ADMIN_SIGNUP=true`** (set on the OWUI deployment via extraEnvVars) lets `/signup` create the very first user as admin even with `ENABLE_SIGNUP=false`. Once any user exists, OWUI's `has_users` short-circuit blocks `/signup` permanently — so the env var is safe to leave on.
- **Migration step (idempotent)** runs on every Job execution. It rewrites the `scout-deploy@internal` bcrypt hash to match the currently derived password. No-op on a clean DB; on an existing cluster it ensures `/signin` works even after a secret-key rotation.
- **Inputs to the Job come from a ConfigMap** the chart renders from inventory: filter function source code, full Scout Explorer model payloads (system prompt inlined, capability flags, suggestion prompts, tool refs), and the PersistentConfig field values. Edit inventory, `make install-chat`, Helm replaces the ConfigMap+Job and the new state takes effect — no SQL surgery.

The full script lives at `helm/open-webui-bootstrap/files/bootstrap.py`. Watch a deploy in real time:

```bash
kubectl logs -n scout-analytics -l app.kubernetes.io/name=open-webui-bootstrap -f
```

### Why signup-or-signin (and not trusted-header SSO)

An earlier design (never merged but its Traefik middleware is still removed by the role) used `WEBUI_AUTH_TRUSTED_*_HEADER`: requests bearing `X-Scout-Bootstrap-*` headers would auto-authenticate as the bot user. A Traefik middleware (`open-webui-strip-bootstrap-headers`) stripped those headers from inbound external traffic so only Ansible (via the in-cluster Service) could pass them.

That design was rejected because setting `WEBUI_AUTH_TRUSTED_*_HEADER` **also** flips OWUI's `auth_trusted_header` *frontend* feature flag. With that flag set, OWUI's `/auth` page auto-calls `/signin` on every page load. External browsers don't carry the bootstrap headers (Traefik strips them, correctly), so the auto-`/signin` always fails — surfacing a "Your provider has not provided a trusted header" error toast to any user whose token expired or who routed through `/auth` (logout, OAuth round-trip, etc.).

Signup-or-signin has no equivalent frontend coupling: the env var that enables it (`ENABLE_INITIAL_ADMIN_SIGNUP`) only affects whether `/signup` accepts the first user — no side effect on user-facing auth UX. The legacy `strip-bootstrap-headers` middleware is removed unconditionally on every deploy via `state: absent` (cheap no-op once gone).

### Migration steps for existing OWUI deployments

**A) Fresh cluster (empty DB).** No action needed. `/signup` runs on the first `make install-chat` and creates `scout-deploy@internal` as admin.

**B) Cluster previously bootstrapped under the trusted-header alpha.** No action needed. The `scout-deploy@internal` user already exists; the migration step rewrites its bcrypt hash to the derived password on the next deploy, then `/signin` succeeds.

**C) Existing OWUI cluster with other users but no `scout-deploy@scout-deploy.local`.** The only case requiring manual action — OWUI's `has_users` check blocks `/signup` once any user exists, so a fresh signup attempt returns `ACCESS_PROHIBITED`. The bootstrap Job will exit non-zero with a diagnostic pointing at this section. **One-time manual step**, signed in as an existing admin:

1. **Admin Panel → Users → Create new user**
2. Email: `scout-deploy@scout-deploy.local` (or whatever `open_webui_bootstrap_email` is set to)
3. Name: `Scout Deploy Bot`
4. Password: any value — the migration step rewrites it on the next deploy
5. Role: `admin`
6. Re-run `make install-chat`

**Rotating `open_webui_secret_key`.** Update inventory, re-run `make install-chat`. The migration step rewrites the bcrypt hash to match the new derived password before attempting `/signin`. No other intervention required.

### Disabling Phase 2 entirely

Set `open_webui_admin_setup_enabled: false` in inventory. The OWUI Helm deploy still runs; the role skips the `open-webui-bootstrap` chart entirely (no Job, no ConfigMap). Useful for environments where the operator wants to manage post-deploy state out of band.

## Post-Deployment Configuration

After deploying via Ansible, configure Open WebUI through the web interface to complete the Scout Explorer setup.

### Prerequisites

- Open WebUI deployed and accessible
- Scout custom models from `scout_models` created (automated by Ansible)
- Trino MCP server deployed (the trino role deploys mcp-trino alongside Trino — see `ansible/roles/trino`)

### Configuration Steps

#### 1. Verify Scout Models (Automated)

Scout custom models are automatically created by Ansible. You can verify they exist:

```bash
kubectl exec -n scout-analytics deploy/ollama -- ollama list
```

You should see each entry from `scout_models` in the list — the defaults ship three (`gemma4-31b-long:latest`, `qwen3.6-long:latest`, `gpt-oss-120b-long:latest`).

**Note:** If you need to manually create or recreate a model, substitute the values from the relevant `scout_models` entry (example below for the default model):
```bash
kubectl exec -it -n scout-analytics deploy/ollama -- sh
cat > Modelfile <<EOF
FROM gemma4:31b
PARAMETER num_predict -1
PARAMETER num_ctx 131072
PARAMETER num_keep 32768
EOF
ollama create gemma4-31b-long:latest -f Modelfile
exit
```

#### 2. Add Trino MCP Tool — automated

Registered automatically by the bootstrap Job (POST `/api/v1/configs/tool_servers`). Override `tool_server_connections` in inventory to add more servers or change the URL; set to `[]` to skip registration.

Verify after deploy:

```bash
kubectl exec -n scout-analytics open-webui-0 -- curl -s http://localhost:8080/api/v1/configs/tool_servers
```

**Note (PersistentConfig semantics):** OWUI stores tool servers, RAG template, default/task model IDs, etc. in its Postgres `config` table as PersistentConfig — naively, env-var changes after first launch are silent no-ops because the admin UI is authoritative.

The bootstrap Job side-steps this by POSTing those fields through OWUI's REST API on every Helm install/upgrade (`/api/v1/configs/tool_servers`, `/api/v1/retrieval/config/update`, `/api/v1/configs/models`, `/api/v1/tasks/config/update`). So `tool_server_connections`, `open_webui_rag_template_file`, `open_webui_default_model_id`, and `open_webui_task_model_id` ARE fully declarative — change inventory, `make install-chat`, done.

Fields that remain genuinely seed-only (set once at first launch via Helm extraEnvVars, expected stable thereafter): `WEBUI_URL` (derived from `server_hostname`), `ENABLE_EVALUATION_ARENA_MODELS`, `ENABLE_SIGNUP`, `ENABLE_LOGIN_FORM`, `ENABLE_COMMUNITY_SHARING`. If you ever need to flip one of these on an existing cluster, delete its row from `config` in OWUI's Postgres and restart the OWUI pod — env will re-seed on next boot. (Wiping the OWUI PVC does **not** re-seed — PersistentConfig lives in Postgres, not the PVC.)

OAuth fields (`OAUTH_*`, `OPENID_*`) are env-only because `ENABLE_OAUTH_PERSISTENT_CONFIG=false` skips loading them from the config table — env always wins.

#### 3. Schema reference in the system prompt

Scout's models run with `function_calling: native`, and OWUI's RAG auto-injection path is gated on `function_calling != 'native'`. Attached knowledge collections only surface via LLM-initiated `list_knowledge` / `query_knowledge_files` tool calls — which thinking-mode models often skip. To guarantee the schema is in context every turn, the database schema reference and charting-output instructions are **inlined into the system prompt file** (`files/scout-system-prompt.md`).

To update the schema reference, edit `files/scout-system-prompt.md` directly. The `docs/source/dataschema.md` doc remains the canonical reference for humans and notebooks; the prompt is a trimmed, query-focused subset.

#### 4. Configure Scout Explorer model — automated

For every `scout_models` entry with a `ui:` block (or for every entry when defaults apply), the role creates/updates a Scout Explorer OWUI model with:

- Display name and description
- System prompt (`files/scout-system-prompt.md`, with the schema reference inlined)
- Tool reference (`server:mcp:{scout-db}` — the Trino MCP server registered in step 2)
- Suggestion prompts (the starter prompts that show up on a new chat)
- Profile image (the Scout logo)
- Capability flags (web_search/code_interpreter/terminal/image_generation disabled for data-handling safety)
- Advanced params: `function_calling: native`, `reasoning_effort: high`, `keep_alive` derived from `preload`
- The corresponding raw Ollama tag (e.g. `gemma4:31b`) is hidden from the picker so users see only the customized Scout Explorer entries

Override any of these via the entry's `ui:` block in inventory:

```yaml
scout_models:
  - base: gemma4:31b
    name: gemma4-31b-long:latest
    preload: true
    ui:
      id: gemma4-31b-long:latest  # defaults to `name`
      name: 'Scout Explorer'      # display name in picker
      description: 'Intelligent data exploration'
      # tool_ids:, suggestion_prompts:, capabilities:, function_calling:, etc.
```

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
kubectl logs -n scout-analytics deploy/open-webui -f | grep "\[ContextSummarization\]"
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
kubectl get pods -n scout-analytics

# Check logs
kubectl logs -n scout-analytics deploy/open-webui
kubectl logs -n scout-analytics deploy/ollama

# Verify Scout models were created (pull + Modelfile create run in one Job)
kubectl get jobs -n scout-analytics -l app=ollama-pull-models
kubectl exec -n scout-analytics deploy/ollama -- ollama list
```

### Common Issues

**Scout model not created:**
- Check job logs: `kubectl logs -n scout-analytics job/<job-name>`
- Verify base model was pulled: `kubectl exec -n scout-analytics deploy/ollama -- ollama list`

**MCP tool not working:**
- Verify MCP server is running: `kubectl get pods -n scout-analytics -l app.kubernetes.io/name=mcp-trino`
- Test connectivity: `kubectl exec -n scout-analytics deploy/open-webui -- curl http://mcp-trino.scout-analytics:8080/health`
- In Open WebUI model settings, ensure Function Calling is set to "Native"

**Authentication issues:**
- Users must have Keycloak roles: `open-webui-user` or `open-webui-admin`

## Related Documentation

- **Main Scout Docs**: https://washu-scout.readthedocs.io/
- **Open WebUI Docs**: https://docs.openwebui.com/
- **Scout Query Prompt**: `files/scout-system-prompt.md`
- **Link Sanitizer Filter**: `files/link_sanitizer_filter.py`
- **Context Summarization Filter**: `files/context_summarization_filter.py`
- **ADRs**:
  - [ADR 0009: Content Security Policy](../../../docs/internal/adr/0009-open-webui-content-security-policy.md)
  - [ADR 0010: Link Exfiltration Filter](../../../docs/internal/adr/0010-open-webui-link-exfiltration-filter.md)
  - [ADR 0014: Context Summarization Filter](../../../docs/internal/adr/0014-open-webui-context-summarization-filter.md)
