# Open WebUI Role

Deploys Open WebUI with Ollama for AI-powered chat interface in Scout.

## Overview

Open WebUI provides a user-friendly interface for interacting with language models via Ollama. In Scout, it's configured with:
- **Keycloak OAuth** for authentication and role-based access control
- **Scout report-viewer tool** (in-image Python tool) for cohort-shaped natural-language querying of radiology reports
- **Valkey** for distributed websocket coordination

## Deployment

```bash
cd ansible
make install-chat
```

The role automatically:
- Creates PostgreSQL database and Valkey instance for Open WebUI
- Deploys Ollama and Open WebUI via Helm
- Pulls configured Ollama models
- Creates each entry in `scout_models` as a Modelfile-derived variant with extended-context parameters baked in. Default ships three: `gemma4-31b-long` (preload, default + task model), `qwen3.6-35b-long` (cold-load, thinking-mode), and `gpt-oss-120b-long` (cold-load, large/cohort-building)
- Mints an admin JWT via the in-cluster Service and uses it to seed filter functions, custom Scout Explorer models, and other OWUI resources via the REST API on every deploy (see [Bootstrap & Migration](#bootstrap--migration) and [Post-Deployment Configuration](#post-deployment-configuration))

### Air-Gapped Deployment

In air-gapped environments (`air_gapped: true`), both model pulling and Scout model creation run on the staging node:

- Models are downloaded to shared NFS storage (`ollama_nfs_path`)
- The air-gapped cluster mounts this NFS read-only
- Scout custom model creation also runs on staging with NFS storage

**Required for air-gapped:**
- `ollama_nfs_path`: Shared NFS path accessible by both staging and cluster

**Timing — staging pull Job vs production pod start:**

The Ollama pod's `lifecycle.postStart` hook preloads `preload: true` models into memory on every pod start. If the staging cluster's pull Job hasn't finished writing models to NFS by the time the air-gapped production Ollama pod first starts, those models won't appear in `ollama list` yet and the hook will skip them — the next user request triggers a cold start, or restart the Ollama pod after the pull Job completes to trigger preload.

Watch the staging pull Job:
```bash
# On the staging cluster:
kubectl get jobs -n scout-analytics -l app=ollama-pull-models -w
```

`preload: false` models always cold-load on first request (intentional — see Multi-model VRAM management below).

### Required Configuration

See `defaults/main.yaml` for all available variables. Key requirements in `inventory.yaml`:

**Required Secrets** (use Ansible Vault):
- `open_webui_postgres_password`
- `open_webui_secret_key` — backs OWUI session signing
- `open_webui_bootstrap_password` — bootstrap admin user's password
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

Ollama's runtime default `num_ctx` is 4096 regardless of the model's native max — that's why each Scout model bakes its `num_ctx` into a Modelfile-derived variant. With multiple `scout_models`, total resident weights + KV cache may exceed GPU memory if all models are kept hot. Set `preload: false` on entries that should cold-load on demand; the bootstrap Job derives each model's `keep_alive` from `preload` (`-1` for resident, `5m` for cold-load) so cold-load models unload when idle and free VRAM for the resident set. Override per-entry via `ui.keep_alive` if needed.

See `inventory.example.yaml` for configuration examples

## Bootstrap & Migration

Admin user creation, filter functions, custom models, and PersistentConfig re-push all run as a Kubernetes Job deployed via a separate Helm chart at [`helm/open-webui-bootstrap/`](../../../helm/open-webui-bootstrap/). The Job is hooked as `helm.sh/hook: post-install,post-upgrade` so it runs after the OWUI chart finishes installing/upgrading, and re-runs on every `make install-chat` (its manifest carries a `checksum/config` annotation over the rendered ConfigMap).

The bootstrap user is `scout-deploy@scout-deploy.local` (configurable via `open_webui_bootstrap_email`).

### How it works

- **The Job uses OWUI's own container image** so the password-migration step can `import open_webui.internal.db` / `open_webui.models.auths` (same modules the app uses), and bcrypt versions match. The image tag is **discovered at deploy time** from the running OWUI Pod, not pinned separately — Renovate bumps `open_webui_helm_chart_version` in `versions.yaml`, the chart's `appVersion` determines the image that runs, and `configure_admin.yaml` reads it back via `kubernetes.core.k8s_info` before installing the bootstrap chart. One pin, no drift.
- **Password comes from `open_webui_bootstrap_password`** in inventory (vault-encrypted), mounted into the Job via `secretKeyRef` from the `open-webui-secrets` Secret. Rotate by updating inventory and re-running `make install-chat`.
- **`ENABLE_INITIAL_ADMIN_SIGNUP=true`** (set on the OWUI deployment via extraEnvVars) lets `/signup` create the very first user as admin even with `ENABLE_SIGNUP=false`. Once any user exists, OWUI's `has_users` short-circuit blocks `/signup` permanently — so the env var is safe to leave on.
- **Migration step (idempotent)** runs on every Job execution. It rewrites the `scout-deploy@scout-deploy.local` bcrypt hash to match the current `open_webui_bootstrap_password`. No-op on a clean DB; on an existing cluster it ensures `/signin` works after a password rotation.
- **Inputs to the Job come from a ConfigMap** the chart renders from inventory: filter function source code, full Scout Explorer model payloads (system prompt inlined, capability flags, suggestion prompts, tool refs), and the PersistentConfig field values. Edit inventory, `make install-chat`, Helm replaces the ConfigMap+Job and the new state takes effect — no SQL surgery.

The full script lives at `helm/open-webui-bootstrap/files/bootstrap.py`. Watch a deploy in real time:

```bash
kubectl logs -n scout-analytics -l app.kubernetes.io/name=open-webui-bootstrap -f
```

### Migration steps for existing OWUI deployments

**A) Fresh cluster (empty DB).** No action needed. `/signup` runs on the first `make install-chat` and creates `scout-deploy@scout-deploy.local` as admin.

**B) Existing OWUI cluster with other users but no `scout-deploy@scout-deploy.local`.** The only case requiring manual action — OWUI's `has_users` check blocks `/signup` once any user exists, so a fresh signup attempt returns `ACCESS_PROHIBITED`. The bootstrap Job will exit non-zero with a diagnostic pointing at this section. **One-time manual step**, signed in as an existing admin:

1. **Admin Panel → Users → Create new user**
2. Email: `scout-deploy@scout-deploy.local` (or whatever `open_webui_bootstrap_email` is set to)
3. Name: `Scout Deploy Bot`
4. Password: any value — the migration step rewrites it on the next deploy
5. Role: `admin`
6. Re-run `make install-chat`

**Rotating `open_webui_bootstrap_password`.** Update inventory, re-run `make install-chat`. The migration step rewrites the bcrypt hash to match the new password before attempting `/signin`. No other intervention required.

### Disabling the bootstrap Job entirely

Set `open_webui_admin_setup_enabled: false` in inventory. The OWUI Helm deploy still runs; the role skips the `open-webui-bootstrap` chart entirely (no Job, no ConfigMap). Useful for environments where the operator wants to manage post-deploy state out of band.

## Reference & Verification

The bootstrap Job leaves OWUI fully configured — there are no manual UI steps on a normal deploy. This section covers what gets set, what's overridable in inventory, and how to verify.

### Smoke test

1. Open the Chat UI → select **Scout Explorer (Gemma 4 31B)**.
2. Ask: *"How many radiology reports are in the database?"*
3. Expect: a `scout-db_execute_query` tool call, the real count from Delta Lake, results in an expandable Output panel.

If the Output panel renders empty: the `tool_result_attr_filter` diagnostic isn't active. Confirm the bootstrap Job created and enabled it:

```bash
kubectl logs -n scout-analytics -l app.kubernetes.io/name=open-webui-bootstrap --tail=200
```

### What gets configured automatically

**Scout Explorer models** — per `scout_models` entry with a `ui:` block: display name, description, system prompt (`helm/open-webui-bootstrap/files/payloads/scout-system-prompt.md`), Scout report-viewer tool reference (`scout_report_viewer_tool`), suggestion prompts, profile image, capability flags (`web_search` / `code_interpreter` / `terminal` / `image_generation` disabled), and advanced params (`function_calling: native`, `reasoning_effort: high`, `keep_alive` derived from `preload`). The raw Ollama tag (e.g. `gemma4:31b`) is hidden from the picker.

**Filter Functions** — installed, configured with valves, and toggled global on every deploy:
- **Link Sanitizer** ([ADR 0010](../../../docs/internal/adr/0010-open-webui-link-exfiltration-filter.md))
- **Context Summarization** ([ADR 0014](../../../docs/internal/adr/0014-open-webui-context-summarization-filter.md))
- **Tool Result Body→Attribute Migrator** — diagnostic workaround for an OWUI 0.9.5 rendering regression (upstream commit 45e49d33e, Apr 2026, moved tool-call results from a `result="..."` attribute on the `<details>` tag into the body; the new path doesn't display). Confirmed still broken in 0.9.6 (`ToolCallDisplay.svelte` byte-identical between v0.9.5 and v0.9.6). When bumping `open_webui_helm_chart_version` past `~14.8.0`, test without the filter (set `enable_active: false` in inventory) — if the regression is fixed, delete the inventory entry, the filter source, and its tests.

**PersistentConfig** — re-POSTed on every deploy: `tool_server_connections`, `DEFAULT_MODELS` (from `open_webui_default_model_id`), `TASK_MODEL` (from `open_webui_task_model_id`).

**Other** — Arena Model evaluation off (`ENABLE_EVALUATION_ARENA_MODELS=false`).

### Common inventory overrides

```yaml
# inventory.yaml

open_webui_default_model_id: 'qwen3.6-35b-long:latest'
open_webui_task_model_id: 'gemma4-31b-long:latest'

# Link Sanitizer allowlist (defaults to server_hostname)
open_webui_link_sanitizer_internal_domains: 'example.com,trusted.partner.com'

# Customize a Scout Explorer model
scout_models:
  - base: gemma4:31b
    name: gemma4-31b-long:latest
    preload: true
    ui:
      id: gemma4-31b-long:latest  # defaults to `name`
      name: 'Scout Explorer'
      description: 'Intelligent data exploration'
      # tool_ids:, suggestion_prompts:, capabilities:, function_calling:, keep_alive: ...

# Skip the bootstrap Job entirely
open_webui_admin_setup_enabled: false
```

### Editing the system prompt

Query instructions, schema reference, and charting-output rules all live in `helm/open-webui-bootstrap/files/payloads/scout-system-prompt.md`. Edit, re-run `make install-chat`; the bootstrap Job updates each Scout Explorer model on the next deploy. `docs/source/dataschema.md` remains the canonical schema reference for humans and notebooks; the prompt is a query-focused subset.

### PersistentConfig — note for the curious

OWUI stores tool servers, default/task model IDs, etc. in its Postgres `config` table as PersistentConfig — env-var changes after first launch are silent no-ops. The bootstrap Job side-steps this by re-POSTing the declarative fields via REST on every deploy. `RAG_TEMPLATE` is intentionally not pushed (Scout Explorer's `function_calling: native` bypasses OWUI's RAG auto-injection — schema docs are inlined into the system prompt instead).

Genuinely seed-only knobs (`WEBUI_URL`, `ENABLE_SIGNUP`, `ENABLE_LOGIN_FORM`, `ENABLE_COMMUNITY_SHARING`) are set via Helm extraEnvVars and only take effect at first launch. To flip one post-launch: `DELETE FROM config WHERE key = '<KEY>'`, restart the OWUI pod, env re-seeds. OAuth fields (`OAUTH_*`, `OPENID_*`) are env-only because `ENABLE_OAUTH_PERSISTENT_CONFIG=false` skips loading them from the config table.

### Example queries

1. `How many CT reports are there?`
2. `How many reports from 2024?`
3. `Find chest X-ray reports with pneumonia diagnosis`
4. `What's the age distribution of patients in the database?`
5. `Show me the top 5 most common modalities by report count`

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
- Check pull/create job logs: `kubectl logs -n scout-analytics -l app=ollama-pull-models`
- Verify base model was pulled: `kubectl exec -n scout-analytics deploy/ollama -- ollama list`

**Bootstrap Job failed (filters / models / tool servers not configured):**
- Check Job logs: `kubectl logs -n scout-analytics -l app.kubernetes.io/name=open-webui-bootstrap`
- Common cause: existing OWUI cluster with other users but no `scout-deploy@scout-deploy.local` row — see Migration steps case B above.

**Report-viewer tool not working:**
- Verify report-viewer is running: `kubectl get pods -n scout-analytics -l app.kubernetes.io/name=report-viewer`
- Test connectivity from OWUI: `kubectl exec -n scout-analytics deploy/open-webui -- curl http://report-viewer.scout-analytics:8000/healthz`
- Confirm the tool function is registered: `kubectl exec -n scout-analytics open-webui-0 -- curl -s http://localhost:8080/api/v1/tools | jq '.[].id'` (the tool is installed declaratively from `open_webui_tool_functions` in inventory).

**Authentication issues:**
- Users must have Keycloak roles: `open-webui-user` or `open-webui-admin`

## Related Documentation

- **Main Scout Docs**: https://washu-scout.readthedocs.io/
- **Open WebUI Docs**: https://docs.openwebui.com/
- **Scout Query Prompt**: `helm/open-webui-bootstrap/files/payloads/scout-system-prompt.md`
- **Link Sanitizer Filter**: `helm/open-webui-bootstrap/files/payloads/link_sanitizer_filter.py`
- **Context Summarization Filter**: `helm/open-webui-bootstrap/files/payloads/context_summarization_filter.py`
- **Tool Result Body→Attribute Migrator (diagnostic — remove when upstream bug is fixed)**: `helm/open-webui-bootstrap/files/payloads/tool_result_attr_filter.py`
- **ADRs**:
  - [ADR 0009: Content Security Policy](../../../docs/internal/adr/0009-open-webui-content-security-policy.md)
  - [ADR 0010: Link Exfiltration Filter](../../../docs/internal/adr/0010-open-webui-link-exfiltration-filter.md)
  - [ADR 0014: Context Summarization Filter](../../../docs/internal/adr/0014-open-webui-context-summarization-filter.md)
