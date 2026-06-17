# Wiring datasets-service into Open WebUI

This service is feature-complete through Phase 5 of `docs/internal/datasets-service-plan.md`. Two pieces still need wiring on the chat side; both are config changes, not code, and they're isolated so they can land in a follow-up PR.

## What's done

| Surface | Where | Notes |
|---|---|---|
| Python service + tests | `datasets-service/` | 30 tests, all passing locally |
| Helm chart | `helm/datasets-service/` | Production-mode (image) + test-mode (source ConfigMap on python:slim) |
| Ansible role | `ansible/roles/datasets_service/` | Provisions Postgres DB+user, distributes staging cert when air-gapped |
| Deployed on dev02 | `make install-datasets-service` | Pod 1/1 Running; smoke-tested via in-pod curl |
| OWUI tool file | `helm/open-webui-bootstrap/files/payloads/scout_datasets_tool.py` | 6 tests, NOT yet referenced in any chart values |

## Pieces still to wire (chat-side config)

### 1. Reference the tool from `tool_server_connections` or load as a Python tool

The new tool file (`scout_datasets_tool.py`) is a standard OWUI Python tool — `class Tools` with `Valves` and `search_reports` / `read_reports` methods. To activate it on a deploy:

- Option A (preferred for now): Upload via OWUI's UI → Workspace → Tools → Import. Quickest to iterate; valves can be tuned per-deploy without redeploying the chart.
- Option B: Add a `tools.json` to the bootstrap-payload pipeline (similar to `filters.json` / `models.json`). Would need a small extension to `bootstrap.py` to POST to `/api/v1/tools/create` instead of `/api/v1/functions/create`.

Defaults bake in the in-cluster service URL (`http://datasets-service.scout-analytics:8000`), so the only valve worth setting per-env is `public_base_url` — the URL the iframe loads from (e.g. `https://datasets.dev02.tag.rcif.io`). Without it, the iframe `src` falls back to the URL the service computed from the create request's host, which may be wrong for in-cluster callers.

### 2. Update the system prompt

`scout-system-prompt.md` currently tells the model to use "Trino MCP" directly. With the new tool in place, the relevant section should reference `search_reports` / `read_reports` and explain when to use each. Not changing the prompt as part of this PR — the existing chat flow keeps working, and the new tool is opt-in until the prompt nudges the model toward it.

### 3. (Optional) Add the service-side AuthZ bridge for OWUI

Today the OWUI tool forwards `__oauth_token__` as `Authorization: Bearer <jwt>`. The datasets-service validates that JWT against Keycloak JWKS (Phase 2). Both halves are in place; what's missing is end-to-end testing because dev02's OWUI doesn't yet know about the tool. Once Option A or B above is done, a real chat query will exercise the path.

## Smoke test today (without OWUI wired)

The service is live on dev02 and reachable in-cluster:

```bash
KUBECONFIG=~/.kube/scout/tagdev-control-02/config kubectl -n scout-analytics \
  exec deploy/datasets-service -- python -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/datasets',
    method='POST',
    data=json.dumps({'sql': 'SELECT message_control_id FROM delta.default.reports_latest WHERE year=2024 LIMIT 5'}).encode(),
    headers={'Content-Type':'application/json','X-Auth-Request-Preferred-Username':'smoketest'}
)
print(urllib.request.urlopen(req).read().decode())
"
```

Returns a real `dataset_id` + 5 sample `message_control_id`s + a `view_url` you can open in a browser (once the ingress + DNS land).
