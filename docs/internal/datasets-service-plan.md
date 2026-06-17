# Datasets Service — Plan and Context Handoff

This document captures decisions, context, and the build plan for the **datasets-service** — a new Scout service that holds cohort/query results outside of OWUI so the chat surface stays light, the LLM context stays clean, and the iframe viewer is a thin client over a real API.

Status as of this writing: scoped and ready to scaffold. Not yet started.

Related upstream design: [`datasets-service-design.md`](./datasets-service-design.md) (Flavin's ideation doc — read first if you're new to the picture).

---

## Why this exists

Three bugs this week all share one root cause: *we ship a large materialized cohort result somewhere it should not live*.

1. **LLM context bloat.** Today the rich-UI iframe HTML lives in the assistant message's `embeds=` attribute, which OWUI sends back to the LLM on every subsequent turn. Band-aided with a new `tool_embeds_strip_filter` (inlet-only, strips the attribute before each LLM call). Works but is a workaround.
2. **Browser/pod heap bloat.** OWUI pod OOM-killed ~4 times this week (confirmed via Prometheus — `kube_pod_container_status_last_terminated_reason` shows only `OOMKilled` in the 24h window). Memory limit bumped 4 GiB → 12 GiB on dev02 as a tactical fix.
3. **Stored-content fragility.** Link-sanitizer corrupting the JSON-encoded embed mid-string (ate the trailing backslash of `\"https://...\"`, JSON parse failed at position ~23,612, iframe vanished on chat re-render). Two-hour debug. Other outlet filters (`tool_result_attr_filter`) also have edge cases that mangle the embed.

Architectural fix: cohort data lives in a dedicated service. OWUI's chat message holds only an ID + URL + small sample. Iframe is a thin client that paginates against the service API.

---

## Decisions (locked)

| Decision | Value | Why |
|---|---|---|
| Service name | `datasets-service` | Aligns with Flavin's design doc; matches the broader concept (cohort is one *kind* of dataset). |
| Backend stack | Python + FastAPI | Reuse `scout_query_tool` logic (label classifier, snippet excerpt, accession resolution); same Trino client pattern; trivial MCP wrapper path later. |
| Database | Postgres on the shared Scout-platform CNPG cluster, new database `datasets` | No new operator; same backup/HA story as other Scout DBs. |
| Trino access | `trino-python-client` via `trino.dbapi.connect(...)` driven by env vars (`TRINO_HOST`, `TRINO_PORT`, etc.) | Matches existing pattern in `scout_query_tool.py`, `bin/query-trino`, Advanced.ipynb. |
| Repo location | In-repo: `datasets-service/` (Python source) + `helm/datasets-service/` (chart) + `ansible/roles/datasets_service/` | Mirrors `extractor/hl7-transformer/` + its chart layout. Single repo CI/version. |
| Python package | `scout_datasets` | Avoids clash with HuggingFace `datasets`. |
| Materialization | At create time. Snapshot identifier list only (`message_control_id[]`). JOIN against `reports_latest` on read. | Cheap (~10 MB for 100k rows); consistent state across a research session; no surprise row drift. |
| Refine semantics | **Option B**: filter the materialized snapshot in place. Strict subset, can never gain rows. | Critical for research-session consistency. Re-query semantics (Option A) would let new rows leak in mid-session. |
| TTL | 30 days, sliding (any read/refine pushes expiry forward) | Most cohorts are throwaway exploration; sliding keeps active research alive without long-tail accumulation. |
| Saved/durable cohorts | Out of scope for v1 | Defer until there's a real "I want to come back to this in 6 months" use case. |
| Ownership | Tied to Keycloak user (`sub` claim) | Chat is the access path, not the owner. Survives chat deletion (until TTL). |
| Auth — browser path | oauth2-proxy + Keycloak in front of own subdomain | Same pattern as Grafana/Jupyter/Superset. Session cookie set on `.dev02.tag.rcif.io` parent domain so SSO is seamless. |
| Auth — OWUI tool path | Forwarded user JWT via `Authorization: Bearer <jwt>` | OWUI tool already reads `__oauth_token__` for XNAT bridge; same plumbing. End-to-end user identity preserved. |
| Ingress hostname | `datasets.dev02.tag.rcif.io` (separate subdomain) | Matches Scout's established per-service-subdomain pattern. Cleaner ops than path-routing on chat hostname. |
| LLM-facing handle | `dataset_id` (renamed from `cohort_id`) | Aligns with the service naming. Matches Flavin's framing. **System prompt + tool docstrings need updating** as part of the OWUI integration phase. |
| Patient cohorts | Defer to v1.1. Schema's `id_column` field is generic so no migration needed when we add `search_patients`. | Real demand isn't proven yet; report cohorts cover today's queries. |
| Viewer | Vanilla HTML + Tabulator initially; swap to React later in the same FastAPI deploy (static bundle) | Python backend doesn't lock us into vanilla; React works fine as a static bundle behind a Python API. |
| Tool count | Keep `search_reports` (build/refine) + `read_reports` (full text per ID). Service-back both. | Outcome-oriented per Anthropic's writing-tools-for-agents guidance. Anti-pattern: CRUD splits (`list_datasets`/`get_dataset`/`filter_dataset`). |

---

## Service API shape

### Endpoints

- **`POST /datasets`** — body: `{ sql: string }`. Service runs Trino, materializes the ID list, stores in Postgres, returns `{ dataset_id, count, sample, summary, view_url }`.
- **`POST /datasets/{id}/refine`** — body: filter spec. Applies predicate to the snapshot in place. Returns a new `dataset_id` with `parent_id` linked.
- **`GET /datasets/{id}`** — metadata: `{ count, kind, parent_id, source_sql, filter_chain, created_at, expires_at, owner_sub }`.
- **`GET /datasets/{id}/rows?page=&limit=&filter=&sort=`** — paginated rows. JOIN against `reports_latest` on read. Filter/sort happen Trino-side.
- **`GET /datasets/{id}/summary`** — aggregate stats (modality breakdown, top dx, date range, counts by label). For both the tool's LLM-bound summary AND the viewer header.
- **`GET /datasets/{id}/view`** — HTML viewer page. Served as the iframe `src`.
- **`GET /datasets/{id}/accessions`** — accession-number list for XNAT export.
- **`GET /datasets/{id}/export.csv`** — CSV download.
- **`POST /datasets/{id}/xnat`** — proxy to XNAT bridge with cohort accessions.

### Postgres schema

```sql
CREATE TABLE datasets (
  id            TEXT PRIMARY KEY,            -- "ds_aB3zX9"
  kind          TEXT NOT NULL,               -- "report" | "patient" (future)
  id_column     TEXT NOT NULL,               -- "message_control_id" | "scout_patient_id"
  id_list       TEXT[] NOT NULL,             -- materialized identifiers, in source-SQL order
  source_sql    TEXT NOT NULL,               -- audit trail
  filter_chain  JSONB NOT NULL DEFAULT '[]', -- refinement history
  parent_id     TEXT REFERENCES datasets(id),
  owner_sub     TEXT NOT NULL,               -- Keycloak `sub` claim
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ NOT NULL,
  last_read_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON datasets (owner_sub, expires_at);
```

If `id_list` ever feels limiting (cohorts >100k rows are common), revisit by moving the list into MinIO (which we already do today for cohort projections) or a separate `dataset_rows` table.

### Tool surface (LLM-facing)

The OWUI tool calls become thin wrappers over the service:

| Tool | LLM input | LLM output |
|---|---|---|
| `search_reports(sql, ...)` or `search_reports(dataset_id, refine_spec)` | SQL string (create) or existing `dataset_id` + refine predicate | `{ dataset_id, count, summary (1 line), sample (5–10 rows), view_url }` |
| `read_reports(ids OR dataset_id + labels + max)` | Specific reports to fetch full text for | Per-report `findings`, `impression`, `dx`, `reason` |

LLM-bound payload target per `search_reports` call: **< 4 KB** (was ~80–200 KB in today's inline-iframe shape).

### Embed shape in OWUI chat

```
Dataset ds_aB3zX9 has 1,247 reports. Top modalities: CT (52%), MR (31%).

<iframe src="https://datasets.dev02.tag.rcif.io/datasets/ds_aB3zX9/view"
        width="100%" height="500"></iframe>
```

Total payload in `message.content`: ~250 bytes. No row data ever stored in OWUI DB, sent over the websocket, or held in the LLM's context.

---

## Phased build plan

### Phase 0 — Scaffolding (~1 hr)
- `datasets-service/` Python skeleton: `pyproject.toml`, FastAPI app, `/healthz` endpoint, Dockerfile.
- `helm/datasets-service/` chart skeleton: Deployment, ClusterIP Service, ConfigMap for env, Secret for shared init secrets.
- `ansible/roles/datasets_service/`: deploy task, defaults, helm value template.
- Image registry: same as other Scout services (`ghcr.io/washu-tag/...`).

**Checkpoint:** Container builds and boots, `/healthz` returns 200.

### Phase 1 — Core API (~1 day)
1. `POST /datasets` — Trino query, materialize, store.
2. `GET /datasets/{id}` — metadata.
3. `GET /datasets/{id}/rows?...` — paginated JOIN-on-read.
4. `GET /datasets/{id}/summary` — aggregate stats.

Postgres schema in (see above). Migrations via Alembic or raw SQL — Alembic if it's not too heavy.

No auth yet — gate-keep with a single shared header secret for dev. Real auth lands in Phase 2.

**Checkpoint:** `curl` end-to-end from inside the cluster: POST creates a dataset, GET paginates rows.

### Phase 2 — Auth (~half day)
- JWT validation against Keycloak JWKS (`python-jose`).
- OWUI tool forwards `__oauth_token__` as `Authorization: Bearer ...`.
- Service rejects expired/missing tokens with 401.
- Per-user isolation: every read filters by `owner_sub`.

**Checkpoint:** Authenticated requests succeed; unauthenticated requests return 401.

### Phase 3 — Deploy on dev02 (~half day)
- Ansible role + helm install.
- New Postgres database on shared CNPG cluster.
- ClusterIP Service for in-cluster traffic.
- Ingress + cert-manager Certificate for `datasets.dev02.tag.rcif.io`.
- New Keycloak client `datasets-service`.
- `make install-datasets-service` target.

**Checkpoint:** `datasets.dev02.tag.rcif.io/healthz` returns 200 behind Keycloak.

### Phase 4 — OWUI tool integration (~half day)
- Add valves to `scout_query_tool`: `datasets_service_url`, `cohort_backend ∈ {"inline" (current), "service" (new)}`.
- When `service` is selected: `search_reports` POSTs to the service, returns `{dataset_id, sample, view_url}`.
- Embed becomes `<iframe src="...view">` (~150 bytes).
- **Rename throughout:** `cohort_id` → `dataset_id` in:
  - `helm/open-webui-bootstrap/files/payloads/scout-system-prompt.md`
  - `helm/open-webui-bootstrap/files/payloads/scout_query_tool.py` (docstrings + parameter names)
  - Prose around it ("the cohort..." → "the dataset...")
- Update `tool_embeds_strip_filter` if needed for the new embed shape (likely no change — it strips both `embeds=` attrs AND ```html fences, and a `<iframe src=...>` in a tiny attribute doesn't trip either).

**Checkpoint:** Flip valve on dev02, run a cohort query, confirm OWUI message has tiny embed and the iframe loads the service viewer.

### Phase 5 — Viewer (~1 day)
- `GET /datasets/{id}/view` returns an HTML page with vanilla JS + Tabulator.
- Calls `/datasets/{id}/rows?page=` on scroll/filter/sort.
- Toolbar: count summary, **Export CSV** (calls `/datasets/{id}/export.csv`), **Send to XNAT** (calls `/datasets/{id}/xnat`).
- Reuse styling/UX from the current TanStack iframe where it makes sense (row tinting, sort indicators, dropdown filters, etc.).

**Checkpoint:** Browser loads iframe, sees real rows, pagination/filter/sort works, CSV downloads.

### Phase 6 — A/B and bake (~ongoing)
- Both `cohort_backend` valves live for at least a week.
- Compare via the existing Chat (OWUI + Ollama) Grafana dashboard:
  - Pod memory (working set vs limit)
  - OOM events
  - Restart count
  - Follow-up + title-gen latency
- Parity check: same SQL produces same row count + same accessions in both backends.
- Cut over to `service` as default; delete inline code path in a follow-up PR.

### Phase 7 — Cleanup (future PR)
- Delete vestigial renderers in `scout_query_tool.py`: `_render_row_cards`, `_render_tabulator`, `_render_cohort_table` (vanilla JS), and `_render_tanstack` once service-backed path is the only path.
- Roughly 1000–1500 LOC of Python deleted.

---

## What this week's work produced that should land on `main` independently

These are bug fixes / additive features that benefit current users today, regardless of whether the dataset service ships. They should be cherry-picked off `awl-cohort-building-chat-poc` into their own branch (`awl-chat-platform-fixes` or similar) and PR'd to `main` ahead of the service work:

1. **`link_sanitizer_filter` backslash terminator fix** — `helm/open-webui-bootstrap/files/payloads/link_sanitizer_filter.py`. Added `\` to URL terminator class. Fixes the disappearing-iframe bug (sanitizer was corrupting JSON-encoded embed mid-string).
2. **`tool_embeds_strip_filter`** — new inlet filter at `helm/open-webui-bootstrap/files/payloads/tool_embeds_strip_filter.py`. Strips `embeds=` attribute (and ```html fences) from assistant content before each LLM call. Reduces follow-up + title-gen latency by ~10×.
3. **OWUI memory limit 4 GiB → 12 GiB** on dev02 — `ansible/inventory.dev02.yaml`. Stops OOM kills until the service ships.
4. **"Chat (OWUI + Ollama)" Grafana dashboard** — `ansible/roles/grafana/templates/dashboards/chat-dashboard.json.j2`. 14 panels, includes Loki-driven Scout activity panels.
5. **`scout_cohort_render` structured log line** in `_render_tanstack`. Feeds the Loki dashboard panels.
6. **Match cell excerpt-around-keyword** — `_excerpt_around_first_match` helper + use in `_render_tanstack`. Genuine UX improvement (keyword stays in the visible part of the cell).
7. **XNAT modal walkthrough when bridge URL unconfigured** — button is still enabled, modal opens, submit shows "bridge not configured" instead of POSTing. Same shape, just survives the empty-config state.

These are the "free wins" — keep them.

---

## What this week's work produced that's experimental / may not survive

Living on `awl-cohort-building-chat-poc` as the working trail of experiments. Don't ship these directly to `main`; the service-backed path supersedes them:

- **`_render_tanstack`** (~470 LOC in `scout_query_tool.py`) — TanStack vanilla rendering. Works. But once the service serves the viewer page, the OWUI tool only emits a tiny URL embed; we don't need to ship the table renderer through the embed attribute at all.
- **`_render_artifact`** — the artifact-fenced-block experiment. Dead end on our setup (OWUI's tool-call body doesn't go through the markdown pipeline that triggers Artifact detection; native function calling clobbers `__event_emitter__`). Kept in the file but not actively routed.
- Cross-message iframe dedup logic — designed around the inline-iframe lifecycle. Becomes obsolete once embeds are URLs.

---

## Repo / file map for the new worktree

When you start the new `awl-datasets-service` worktree, the relevant files to look at first:

| File | Why |
|---|---|
| `helm/open-webui-bootstrap/files/payloads/scout_query_tool.py` | Source of code to lift: Trino client setup, label classifier, snippet excerpt logic, accession resolution, search_reports + read_reports signatures. |
| `helm/open-webui-bootstrap/files/payloads/scout-system-prompt.md` | Will need `cohort_id` → `dataset_id` rename in Phase 4. |
| `ansible/roles/jupyter/files/agent-context/bin/query-trino` | Reference: the canonical minimal Trino client snippet in Scout. |
| `ansible/roles/extractor/templates/hl7-transformer.values.yaml.j2` | Reference: how a Python-backed Scout service is configured via Helm + Ansible. |
| `extractor/hl7-transformer/pyproject.toml` | Reference: Python package layout for a Scout service. |
| `ansible/roles/scout_common/defaults/main.yaml` | `chatbot_namespace`, `trino_namespace`, etc. — env-var sources. |
| `docs/internal/datasets-service-design.md` | Flavin's ideation doc — the design context for this work. |

For Phase 4 (OWUI tool integration), you'll need to update:
- `scout_query_tool.py` — add valve, route `search_reports` through service when enabled, rename `cohort_id` → `dataset_id`.
- `scout-system-prompt.md` — rename `cohort_id` → `dataset_id`, update example tool calls.
- `tool_embeds_strip_filter.py` — likely no change, but verify the new tiny embed shape passes through cleanly.

---

## Open questions for the Monday team meeting

These are decisions that could re-scope the plan; worth getting team alignment before Phase 0 starts.

1. **Naming check with Flavin**: he wrote `datasets-service-design.md`. Confirm chart/service/API path names match his expectations. Also confirm the LLM-facing rename `cohort_id` → `dataset_id` is desired (researchers may still say "cohort"; we could keep both terms in the UI prose and just align the API/handle).
2. **Are we sure Python?** Other Scout backends (orchestrator, hl7log-extractor, launchpad) are TypeScript. Strategic direction = consolidate on TS, or accept Python for this since `scout_query_tool` is Python?
3. **Where does the CNPG database live?** Probably on the existing OWUI Postgres cluster, but worth confirming with whoever owns CNPG operations — capacity, backup schedule, etc.
4. **Viewer rendering tech**: vanilla HTML + Tabulator now, React later — but if the team has a "we want React from day 1" opinion, surface it before Phase 5 so we don't ship a viewer twice.
5. **Sample selection strategy** in `search_reports` response: random N? First N by date? 5 positive + 5 flagged? Affects how useful the LLM's reasoning over the sample is. Concrete choice needed before Phase 4.

---

## Suggested branch + worktree structure

```
main
 ├── awl-chat-platform-fixes        ← cherry-pick the "what survives" items above; PR to main
 ├── awl-datasets-service           ← new worktree, this plan executes here
 └── awl-cohort-building-chat-poc   ← original; preserves the UI experiments as a trail
```

For the new worktree:

```
git worktree add ../scout-datasets-service main -b awl-datasets-service
cd ../scout-datasets-service
# Then start Phase 0 here.
```

If you want the chat-platform-fixes branched out simultaneously:

```
git worktree add ../scout-chat-platform-fixes main -b awl-chat-platform-fixes
cd ../scout-chat-platform-fixes
# Cherry-pick the link sanitizer fix, tool_embeds_strip_filter, memory bump,
# chat dashboard, match-cell excerpt, etc. from awl-cohort-building-chat-poc.
```

---

## Total scope estimate

- Phase 0-3 (scaffolding through deploy with auth): **~2-3 days**
- Phase 4-5 (OWUI integration + viewer): **~1.5 days**
- Phase 6 (A/B): ongoing background work for ~1 week
- Phase 7 (cleanup of vestigial code): a few hours, after A/B concludes

So **~1 week of focused work to ship Phase 0-5**, then a week of bake-off before cutting over.

---

## Quick reference — week-of-2026-06 work history

For context if anyone reviewing this needs to trace back the "why":

- Multiple OWUI iframe lifecycle bugs traced + worked around (link sanitizer, embed-attribute size, tool_result_attr_filter conflicts)
- TanStack vs vanilla cohort-table A/B → TanStack chosen as active renderer
- Artifact panel attempt → dead end (OWUI's tool_calls body skips markdown pipeline; native function calling clobbers `__event_emitter__`)
- Memory bump 4Gi → 12Gi after 4 confirmed OOM kills
- Grafana dashboard with 12 cAdvisor/kube-state-metrics panels + 2 Loki Scout-activity panels
- Inlet filter `tool_embeds_strip_filter` to slim LLM context per turn
- 6 deploy cycles tracked: `bnedzp81a`, `b64cfx7aq`, `by7q9yeor`, `bmbs29iof`, `btdpwzn54`, `b2rraepqw`, `busx2bshs` — all clean

Conclusion: the inline-iframe pattern is hitting its architectural ceiling. Datasets service is the structural fix.
