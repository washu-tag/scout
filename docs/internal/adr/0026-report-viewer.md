# ADR 0026: Report Viewer Service for Chat

## Status

Pending.

## Context

Scout's chat surface (Open WebUI) is an effective natural-language entry point
for cohort building. Researchers describe what they want, an LLM translates
that to Trino SQL against the Scout Delta Lake with radiology reports, the 
user refines via follow-up questions, and have a research cohort emerge in a 
conversational flow. The problem is that the chat context isn't designed for 
large data interaction. 

The problem this ADR addresses is the gap between the LLM's ability to generate 
SQL for large cohorts and the researcher's need to view, refine, and export those 
cohorts in a usable way.

These requirements together motivate a service that lives next to chat and helps with
the heavy lifting of viewing/browsing, refinement, and export for chat-built cohorts.

## Decision

Build **report-veiwer-service** as a Scout-owned microservice that fills this gap.

### Tool surface (LLM-facing)

The LLM interacts with the service through three intent-shaped tools.
These will be Open WebUI natvie tools that are thin wrappers over new
report-viewer API endpoints.

| Tool | Purpose | Saves a search? |
|---|---|---|
| `scout_find_reports` | Find reports matching clinical criteria and/or a CSV of identifiers attached in chat; surface them in the iframe viewer | Yes |
| `scout_get_reports` | Fetch full content of specific reports by ID, return to LLM for context | No |
| `scout_query_sql` | Run an ad-hoc analytical query (counts, breakdowns, distinct lists) and return rows to LLM | No |

`scout_find_reports` accepts a SQL query plus metadata (plain-language explanation, 
highlight terms) emitted by the LLM, validates and saves it as a named search, and 
returns a sample of matching rows plus summary context for the LLM to continue the conversation. 
The resulting search is browseable in an iframe above the LLM's reply, with Export to CSV and Send to XNAT actions.

A *search* is the persisted artifact produced by `scout_find_reports`.
It has a stable `id`, a browseable iframe view, and supports
the export CSV and Send-to-XNAT functionality. `scout_get_reports` and
`scout_query_sql` execute transient Trino reads and do not persist state.

### Service shape

- **Backend**: Python (FastAPI), exposes `/api/...` for all three operations
  and the search resource endpoints.
- **Frontend**: React + Vite + TypeScript + Tailwind + TanStack Table +
  TanStack Query + React Router. Built as static files via Vite, bundled
  into the Python container, served by FastAPI's `StaticFiles` at `/`.
  No Node runtime in production. The SPA does NOT hold a Keycloak token;
  browser auth comes from oauth2-proxy at the ingress (see Authentication).
- **Deployment**: Single Kubernetes deployment in `scout-analytics`. One
  ingress, one Helm chart, one Keycloak client (`report_viewer_svc`,
  confidential service-account used for Trino impersonation).

### How the chat integrates

For a cohort-building question:

1. Researcher asks the cohort question in chat.
2. Open WebUI's native `scout_find_reports` tool calls `POST /api/searches` and
   `Authorization: Bearer` carrying the user's Keycloak access token.
3. Service validates the SQL via a small `LIMIT 5` sample, runs
   `SELECT COUNT(*) FROM (<sql>) sub` to cache the row count, persists
   the SQL + metadata to Postgres, and returns:
   - `id`
   - `count`
   - a 5 to 10 row sample (with snippet text around matching terms) for immediate LLM context
   - an LLM-bound summary markdown blob
   - a `view_url` pointing at the SPA route for this search
4. An OWUI filter (`search_iframe_lift_filter.py`) renders the SPA in an
   iframe below the LLM's reply.
5. The LLM uses the sample plus summary to continue the conversation; the
   user uses the iframe to browse the full cohort table, refine with follow-up questions, 
  and trigger bulk actions like export CSV and Send-to-XNAT.

The iframe is served from the chat host's same-origin alias
(`https://chat.<env>/spa/searches/{id}` proxied to report-viewer).
Same-origin is required so the SPA can:
- read `window.frameElement` to resize the iframe to fit content (OWUI
  0.9.5 cross-origin embeds pin at 150px),
- `fetch('/api/searches/{id}/rows')` and `fetch('/api/searches')` with the
  chat-host session cookie, no CORS preflights, no token plumbing in
  the SPA bundle.

OWUI's `message.embeds` iframe sandbox must therefore include
`allow-same-origin` (without it the SPA loads as a unique opaque origin
and same-host fetches still send as cross-origin → cookies omitted).
The OWUI bootstrap is configured to apply this attribute set for our
embed URL; document any future OWUI upgrade-time changes to that
sandbox policy as a regression risk for this flow.

For a non-cohort question (specific report lookup, or ad-hoc count/breakdown):

- OWUI's `scout_get_reports` or `scout_query_sql` tool calls the corresponding endpoint.
- Service runs the Trino query and returns rows directly to the tool, which
  formats them into chat-friendly markdown for the LLM. No search, no iframe,
  no persistence.

### External ID list as an entry point

Researchers commonly start a study from a list of identifiers received from
elsewhere (an IRB submission, a prior cohort built in another tool, a
collaborator's spreadsheet). They want to attach that file in chat and have
the resulting cohort behave like any other.

The LLM does NOT read the file. Forcing thousands of IDs through the chat
context blows the LLM's context window and overflows Trino's 1 MB SQL text
limit. Instead:

1. Researcher attaches a CSV or Excel file with a column of MRNs or accession numbers, 
   and asks the LLM to build a cohort from that list.
2. The `scout_find_reports` tool reads the file bytes server-side (not the LLM) and POSTs it
   as multipart/form-data to `POST /api/searches/from-file` (see the Endpoints section for
   the tool-to-API mapping).
3. The service parses the file, identifies the ID column (heuristic on
   header names like epic_mrn, patient_id, etc ), validates each ID
   against `reports_latest_epic_view` on the appropriate column, and
   encodes the matched IDs into a saved `WHERE <id_col> IN ('a','b',...)`
   SQL. From the service's point of view this is the same as any other
   saved cohort.
4. From that imported cohort, normal refinement / export / Send-to-XNAT paths
   apply.

`POST /api/searches/from-file` reports a match count back to the LLM
("uploaded N IDs, M matched reports in the lake, K unmatched"), surfaced
in the chat response so researchers know how many of their inputs resolved.

Open implementation questions (resolved during implementation, not in this
ADR): upload size cap, behavior when the file mixes ID types, partial-match
reporting format for unmatched IDs (return as a side payload? attach as a
separate downloadable list?).

### Just-in-time cohort evaluation

A cohort is a saved SQL query plus minimal metadata — nothing about
which rows match is stored. The query runs against Trino each time the
SPA fetches rows, exports CSV, or fetches a single report.

- **Postgres `searches` table** holds: `id`, `owner_sub`,
  `id_column`, `sql`, `sql_explanation`, `highlight_terms`,
  `highlight_diagnosis`, `row_count` (cached at create time via one
  `SELECT COUNT(*) FROM (<sql>)`), `owui_chat_id`, `created_at`.
- **No Delta side-table**, no Postgres `id_list`, no row_metadata blob.
  Every read wraps the saved `sql` as a subquery and applies
  pagination/sort/filter at the Trino layer.
- Imported ID lists (CSV upload) become a saved `WHERE <id_col> IN
  ('a','b','c',...)` SQL — the IDs are validated against
  reports_latest at import time, then encoded into the SQL itself.
  Same single-source-of-truth as any other cohort.

**Trade-off accepted:** every `/rows` page costs one Trino scan
(~2-5s for a typical cohort REGEXP_LIKE search). Materializing rows
into a Delta side-table would be faster on re-read but adds a dual-write
code path, NetworkPolicy ingress to trino-rw, drift between the saved
SQL and the materialized rows, and refinement-chain complexity. We
prefer operational simplicity over read latency — if pagination ever
becomes painful, layer a Trino result cache or a short-TTL per-cohort
materialization without changing the API.

### Negation and clinical filtering

Cohort SQL emitted by the LLM applies all clinical filters in Trino directly,
following the patterns documented in
[`scout-system-prompt.md`](../../../helm/open-webui-bootstrap/files/payloads/scout-system-prompt.md):

- Boolean and synonym alternation expressed in `REGEXP_LIKE` against parsed
  report sect ions (`report_section_impression`, `report_section_findings`)
- Sentence-bounded negation removal via `NOT REGEXP_LIKE` with `[^.;:]{0,40}` scope
- Historical and possibility exclusion via the same pattern
- ICD-code union arm via `any_match(diagnoses, ...)` for coded cases

### ICD-code override pattern

When the LLM emits a query that unions an ICD-code arm with a text-match
arm, the negation filter applies ONLY to the text arm. A coded diagnosis
is a stronger signal than a text mention, so a row with a matching ICD
code is included even if the text contains a negation phrase.

The SQL structure is nested, not top-level:

```sql
WHERE (
    -- ICD path: unconditional inclusion
    any_match(diagnoses, d -> d.diagnosis_code LIKE 'I26%')
    OR (
        -- Text path: conditional on no negation
        (REGEXP_LIKE(report_section_impression, '...pulmonary embolism...')
         OR REGEXP_LIKE(report_section_findings, '...pulmonary embolism...'))
        AND NOT REGEXP_LIKE(report_section_impression,
              '(?is)(?:no|without|...)[^.;:]{0,40}pulmonary embolism')
        AND NOT REGEXP_LIKE(report_section_findings,
              '(?is)(?:no|without|...)[^.;:]{0,40}pulmonary embolism')
    )
)
```

### Interpretable SQL

The LLM-emitted SQL is a researcher-facing artifact, not an internal
implementation detail. The `scout_find_reports` tool POSTs two parallel inputs
to `POST /api/searches`:

- `sql` — the Trino SQL to execute, verbatim.
- `sql_explanation` — a structured plain-language description of what the
  SQL does (positive criteria, exclusions, scope choices), emitted by the
  LLM alongside the SQL.

Both are persisted on the `searches` row. The SPA reads them to render
researcher-facing filter context. The system prompt instructs the LLM to
surface filter variations in the chat response when a default might not
match the researcher's intent, and to accept natural-language refinement
directives ("include historical mentions", "drop that exclusion") that
toggle the corresponding clauses in the re-emitted SQL.

The SPA table will have an "explain filters" UI that surfaces the `sql` and 
`sql_explanation` side by side, so researchers can understand how the LLM 
translated their request into SQL, and how to phrase follow-up questions to 
tweak the filters.

### Authentication

Two callers, two inbound paths, one outbound Trino auth pattern.

**Inbound, caller authenticates to the service:**

- **Browser SPA / iframe**: oauth2-proxy gates the chat-host alias and
  the rvs ingress at the Traefik layer (approval gate per
  [ADR 0003](0003-oauth2-proxy.md)). It forwards identity-only via
  `X-Auth-Request-Preferred-Username`. No access token is forwarded.
  FastAPI reads the header and resolves the user.
- **OWUI tool runtime**: in-cluster POST to the service with
  `Authorization: Bearer <__oauth_token__>` (the user's Keycloak access
  token, minted by the `open-webui` client). FastAPI validates the JWT
  against Keycloak JWKS (signature + iss + exp + `aud=report-viewer`,
  stamped by the `report-viewer-audience` client scope on the OWUI
  client) and resolves the user from `preferred_username`.
  NetworkPolicy restricts Bearer-bearing in-cluster traffic to OWUI
  pods so the header can't be forged from elsewhere in the cluster.

Both paths produce the same `User(sub=...)` model. The user's JWT is
NEVER forwarded onward to Trino; it's used only for inbound AuthN.

**Outbound, service authenticates to Trino:**

The service follows the impersonation pattern from
[ADR 0022](0022-trino-auth-and-impersonation.md) (same as Superset
and Voila):

- New confidential Keycloak client `report_viewer_svc` with
  `trino-audience` in `defaultClientScopes`. Service Accounts enabled;
  no user-facing flows.
- The service mints a Bearer via `client_credentials` against Keycloak
  on demand, caches it in-process, and refreshes when ⅕ of the lifetime
  remains (ADR 0024 ratio). Single-flight under a `threading.Lock` so a
  burst of concurrent queries triggers one refetch.
- Every Trino call goes out as `Authorization: Bearer <svc-token>` +
  `X-Trino-User: <user.sub>`. OPA's `trino_service_principals` set
  includes `report_viewer_svc` and grants it `ImpersonateUser`; the
  per-user row filters and column masks then evaluate against the
  impersonated identity.

### REST API endpoints

The REST API is the underlying service contract. It's resource-shaped (CRUD
on searches plus RPC operations on reports), independent of the
intent-shaped tool surface the LLM sees. Multiple tool consumers can hit
this same API; today the only one is the OWUI in-image tool.

**Search CRUD** (the search resource):

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/searches` | Save a SQL-defined search. Body: `{ sql, sql_explanation, highlight_terms, owui_chat_id }`. The service stores the SQL, runs `SELECT COUNT(*) FROM (<sql>) sub` to cache the count, and returns `id + count + sample + summary + view_url`. The CSV-upload path uses a sibling endpoint `POST /api/searches/from-file` that validates the supplied identifiers against `reports_latest` and saves a `WHERE <id_col> IN (...)` SQL — same downstream shape. No materialization in either case. |
| GET | `/api/searches/{id}` | Search metadata (count, owner, timestamps). |
| GET | `/api/searches/{id}/rows?page&limit&sort&filter` | Paginated rows for the SPA table. Server-side filter and sort on Trino-native columns. |
| GET | `/api/searches/{id}/csv` | Streaming chunked CSV download (mirrors `/rows` — paginated JSON vs. streamed CSV). |
| GET | `/api/searches/{id}/accessions` | Distinct accession-number list for the Send-to-XNAT handoff. |
| DELETE | `/api/searches/{id}` | Owner-scoped delete. Returns 204; 404 for unknown id or wrong owner. |

**Reports operations** (RPC-style; don't produce a resource):

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/reports/query` | Run ad-hoc analytical SQL. Returns rows up to `row_cap` (default 500). No persistence. (Maps to the `scout_query_sql` tool surface.) |
| POST | `/api/reports/read` | Fetch specific reports by ID array. Returns matching rows directly. No persistence. (Maps to the `scout_get_reports` tool surface.) |

**SPA and infrastructure:**

| Method | Path | Purpose |
|---|---|---|
| GET | `/spa/*` | Browser-loadable HTML + assets; FastAPI mounts the SPA build under `/spa/` and falls back to `index.html` for unknown paths so React Router's client-side routes (e.g. `/spa/searches/{id}`) work on direct navigation. |
| GET | `/healthz` | Liveness. |

**Tool → API mapping** (so the indirection is documented in one place):

| Tool | REST call |
|---|---|
| `scout_find_reports` (criteria) | `POST /api/searches` with `{ sql, sql_explanation, highlight_terms, parent_id? }` |
| `scout_find_reports` (file) | `POST /api/searches/from-file` (multipart with file plus optional column hints) |
| `scout_get_reports` | `POST /api/reports/read` |
| `scout_query_sql` | `POST /api/reports/query` |

### Refinement

When a user asks to narrow an existing cohort ("only MRs", "drop the
under-18 patients"), the LLM writes a fresh `scout_find_reports`
call: the original conditions plus the new constraint. The service
saves the new SQL as its own search and renders the new cohort. The
old search is unchanged; lineage is captured as `parent_id` on the
new row purely for the SPA homepage to group cohorts.

Refinement has no cross-cohort SQL dependency — each cohort is a
standalone saved query. The LLM is responsible for keeping the
original conditions intact when adding a filter, which the system
prompt enforces with examples.

### Snippet + positive-diagnosis feedback to the LLM

The LLM passes `highlight_terms` (e.g. `["stroke", "cerebral
infarction", "cva"]`) on every clinical-finding cohort. The service
uses those terms to feed evidence back to the LLM in the sample rows
returned alongside the cohort summary:

  * **`snippet`** — ±80 char excerpt around the first highlight-term
    match in `report_section_impression` / `report_section_findings`
    / `report_text`. Shows the LLM *why* each sample row was included
    without dumping the full report into its context.

  * **`positive_dx`** — for each sample row, the diagnoses whose code
    or text matches any highlight term. Surfaces ICD-level evidence
    (e.g. "I63.9 — Cerebral infarction, unspecified") so the LLM can
    cite specific coded diagnoses when summarizing.

These are extracted at cohort-creation time and only attached to the
N sample rows that go back to the LLM — never stored. Since the
LLM-generated SELECT typically *doesn't* include `report_section_*` /
`diagnoses`, the service does ONE additional small Trino query
against `reports_latest` with `WHERE <id_col> IN ('id1', ..., 'idN')`
(N=5 literal IDs from the sample) to fetch those columns, then merges
in the snippet + positive_dx fields.

Why the feedback matters: without it, the LLM can SQL-match a row but
has no idea *why* — no evidence to reason about. With snippets +
positive_dx in the sample, the LLM can spot-check that "stroke" is
mentioned in context (not just inside HISTORY), and that the row also
carries an I63 diagnosis code, before deciding whether to summarize
or recommend refinement.

### OWUI new-user iframe-sandbox seeding

Out-of-the-box, every new OWUI user has `iframeSandboxAllowSameOrigin`
and `iframeSandboxAllowForms` set to false (per-user UI defaults; no
admin-global override exists in OWUI 0.9.6). Without those flags the
chat's `message.embeds` iframe loads as a unique opaque origin —
`window.frameElement` reads as cross-origin, same-host fetches drop
the chat session cookie, and the report viewer breaks in a way that
forces every researcher to dig into *Settings > Interface > Artifacts*
and flip both toggles before their first search works.

To avoid that, report-viewer exposes `POST /webhooks/owui-new-user`
as the receiver for OWUI's admin signup webhook (configured via
OWUI's `WEBHOOK_URL` PersistentConfig field). On a signup event the
receiver does one Postgres statement against OWUI's own database
that merge-sets both flags to true on the new user's `settings` JSON
column.

**Why direct DB, not OWUI's HTTP API?** OWUI 0.9.6 has no admin
endpoint to write another user's UI settings. Upstream 
[open-webui/open-webui#20770](https://github.com/open-webui/open-webui/pull/20770)
would add the missing admin endpoint but the contributor states it
"probably won't get merged anytime soon." That leaves direct DB
write as the only path that lands the setting BEFORE the user's
first page hydrate (the in-tool / first-chat-filter alternatives
have a race against OWUI's client-side settings cache).

**Why this isn't a race.** OWUI's signup webhook is `await`ed inside
the OAuth callback handler (`open_webui/utils/oauth.py` ~line 1745).
That means OWUI's server holds the response open until the webhook
returns. Sequence:

1. Keycloak redirects back with auth code
2. OWUI creates the user record
3. OWUI calls our webhook (blocking)
4. Our receiver merge-updates `"user".settings`
5. Webhook returns, OWUI redirects the browser
6. Browser's first page hydrate reads settings with flags already true
7. First search renders iframe with `allow-same-origin` from the very
   first message — no manual toggle, no page reload required

**The SQL.** One statement, idempotent (handles null `settings`,
missing `ui` sub-object, missing keys via `jsonb_set(create_missing=true)`):

```sql
UPDATE "user"
   SET settings = jsonb_set(
       jsonb_set(
           COALESCE(settings::jsonb, '{}'::jsonb),
           '{ui,iframeSandboxAllowSameOrigin}', 'true'::jsonb, true
       ),
       '{ui,iframeSandboxAllowForms}', 'true'::jsonb, true
   )::json
 WHERE id = $1;
```

**Deploy-time wiring:**

1. **OWUI Postgres connection** — report-viewer mounts a
   `REPORT_VIEWER_OWUI_DATABASE_URL` env via `secretKeyRef` from
   `open-webui-secrets`, key `DATABASE_URL` (OWUI's own Postgres
   credential, reused for now — see the least-privileged-role
   follow-up below).

2. **OWUI admin-settings `WEBHOOK_URL`** — set to
   `http://report-viewer.<chatbot_namespace>:8000/webhooks/owui-new-user`.
   PersistentConfig value pushed by the open-webui-bootstrap chart on
   every install-chat. Note that OWUI's `validate_url()` SSRF guard
   rejects RFC1918 hostnames by default — we set
   `ENABLE_RAG_LOCAL_WEB_FETCH=true` on the OWUI deployment per
   upstream [#24587](https://github.com/open-webui/open-webui/pull/24587)
   to allow the in-cluster Service URL.

Until #1 lands, the receiver path is reachable but returns 503 ("OWUI
database URL not configured") and no settings are written.

**Webhook trust model.** The receiver has no application-layer
authentication, by design. OWUI's `POST /api/webhook` admin endpoint
only accepts `{"url": "..."}` — no headers, no signing key, no caller
identity — so no Keycloak service-principal or shared-secret pattern
is wireable on the OWUI side (we'd have to fork OWUI). Protection
comes from three layers stacked instead:

1. **Ingress path enumeration.** `helm/report-viewer/templates/ingress.yaml`
   only routes `/api/*` and `/spa/*` externally. `/webhooks/*` (along
   with `/metrics`, `/healthz`, `/docs`) is not in the Ingress path
   list, so Traefik returns 404 for any external request. The webhook
   is only reachable via in-cluster Service DNS.

2. **NetworkPolicy.** In-cluster ingress to the pod's port 8000 is
   restricted to Traefik and `open-webui` peers (and Prometheus, for
   the scrape). Same precedent as trino-rw (ADR 0019), which gates
   its in-cluster surface to the hl7-transformer pod with no
   application-layer credential.

3. **Operation narrowness.** The receiver sets two boolean UI flags
   (`iframeSandboxAllowSameOrigin`, `iframeSandboxAllowForms`) to
   `true` on the user_id in the payload. The response is 204 No
   Content whether the user exists, doesn't exist, or already has
   the flags set — no oracle, no data leak. Calls against missing
   users are logged no-ops. Setting the flags `true` is the desired
   state; an attacker calling the endpoint either helps a user
   reach that state or hits a no-op. SQL is parameterized.

**Postgres role and secret ownership.** The receiver connects as a
dedicated `rvs_owui_settings_writer` role granted only `SELECT (id,
settings)` and `UPDATE (settings)` on `"user"`. No access to other
tables, no PII reads outside the settings column. The role is
provisioned by the open-webui Ansible role on every install-chat,
so it stays in sync with the openwebui database lifecycle. The
connection URL is rendered into report-viewer's own Helm Secret from
inventory vars, not borrowed from `open-webui-secrets`, so each
service owns its own credentials.

**Note on the previously-documented "auto-enable" role flip.** Earlier
drafts of this ADR described the receiver as flipping the new user's
`role` from pending → user via OWUI's HTTP admin API. That requirement
turned out to be fictional — users in the current realm config are
already auto-enabled via the Keycloak SSO + OWUI OIDC flow, no manual
admin approval needed. The receiver's only legitimate job is seeding
the iframe UI flags described above.

### Observability

The service follows the Scout observability conventions:

- **Metrics**: Prometheus scrapes a `/metrics` endpoint with service-specific
  counters and histograms (search creation, Trino/Postgres latency) alongside 
  the standard HTTP histograms from `prometheus-fastapi-instrumentator`. Labels 
  are kept low-cardinality; user-derived values (owner, search id) stay out of labels.
- **Logs**: structured JSON to stdout, picked up by Loki. Search events are
  stamped with contextual fields (search id, count, owner) so log lines
  correlate to specific operations.
- **Grafana dashboard**: an operator dashboard packaged with the rest of the
  Scout dashboards, surfacing search-creation rate, Trino/Postgres latency
  percentiles, search-size distribution, and import match rates.

Distributed tracing is an open question, but other Scout services have not adopted 
it so it's not a blocker.

## Related

- [ADR 0003: OAuth2 Proxy approval gate](0003-oauth2-proxy.md)
- [ADR 0015: Renovate dependency management](0015-renovate-dependency-management.md)
- [ADR 0019: Read-write Trino instance for view DDL](0019-trino-rw-instance-for-views.md)
- [ADR 0020: Trino authorization architecture](0020-trino-authz-architecture.md)
- [ADR 0022: Trino authentication and impersonation](0022-trino-auth-and-impersonation.md)
- [ADR 0023: Trino view security model](0023-trino-view-security-model.md)
- [ADR 0024: SDK Trino token refresh](0024-sdk-trino-token-refresh.md)
- [`scout-system-prompt.md`](../../../helm/open-webui-bootstrap/files/payloads/scout-system-prompt.md): LLM cohort-building patterns including negation removal


## TODOs

- Test on preprod

- Performance test large chat with large queries. Currently tell LLM to use a 50k limit.

- Test with Qwen 3.6

- Test aggregate queries and non-cohort queries from the LLM.

- Review the scout query OWUI tool implemntation.

- Review React App SPA implementaition.

- Prompt review needed, too much irrelevant stuff has built up while iterating on the service. 
 - when to use reports_latest vs reports_latest_epic_view and other Scout tables and views.

- Report snippets are not being given to the llm for context and the diagnosis codes are pressented outside of the results table given to the llm. This is a regression from the POC, the llm needs some report context.

- The LLM is picking too many highlight terms, maybe limite to 3 - 5.

- Update observability stack

-  PHI in searches.sql — imported CSV cohorts persist WHERE epic_mrn IN ('123',...) clear-text in Postgres. Retention/encryption decision needed.

- Test CSV Upload and Download.

- Send to XNAT handoff with IQ plugin. Not needed for the initial release but will be needed soon after.

- **Service owns its own public URL; drop the tool's `public_base_url` valve.** Today `routes/searches.py:_view_url` builds `view_url` from `str(request.base_url)`, which is the URL the inbound request came in on. When the OWUI tool dials the in-cluster Service DNS (`http://report-viewer.scout-analytics:8000`), that's what gets stamped into the response — useless to a browser. The tool's `public_base_url` valve (`ansible/roles/open-webui/defaults/main.yaml`) exists solely to swap scheme+host back to the public ingress host. Two soft problems with the current shape: (a) deriving response URLs from a caller-supplied `Host` header is the canonical host-header-injection pattern (no direct exploit today since the response goes back to the requester, but if shared-cohort flows surface a `view_url` to a different user the poisoned URL becomes a phishing vector); (b) every future caller has to know the override trick.

  Fix: add a `REPORT_VIEWER_PUBLIC_URL` env var on the service (rendered in `ansible/roles/report_viewer/templates/values.yaml.j2` from the same `report_viewer_host` already used by the ingress), have `_view_url` use it instead of `request.base_url`, drop the `public_base_url` valve from the tool. After that the tool's only URL knob is the in-cluster service URL — no naming pair to bikeshed.

- "POST /api/searches/from-file" i thought we discused sending the file to the service directly instead of OWUI parsing it? I think the logic is better handeled in the service and not in OWUI.

- Why row cap in the json POST /api/reports/query:
  { "sql": "SELECT modality, COUNT(*) FROM reports_latest GROUP BY 1", "row_cap": 500 } - this should be handled by a LIMIT clause or we have a hard limit on the backend for the number of rows returned this is a bit of an odd pattern to have in the API.

- Review the Markdown summary shapping.

- Embed vs non-embed view -> not a big use case for non-embed, claude is putting to much into this distinciton, need to remove that notion from the code.

- **Playwright canary for the iframe-sandbox seeding flow.**  Unit-testing `owui_webhook.py` against our own assumptions won't catch regressions in OWUI itself (table/column rename, payload-shape change, flag name change, OWUI stops `await`-ing the webhook in the OAuth callback, an admin-global default makes the seeding moot, etc.). Add a Playwright test in `tests/auth/tests/` that signs in a freshly-provisioned Keycloak user, lets the OWUI signup flow run, then either calls `GET /api/v1/users/user/settings` as that user or inspects the rendered iframe's `sandbox` attribute, and asserts `iframeSandboxAllowSameOrigin` is true. Random username per run (e.g. `iframe-seed-test-{uuid}@scout.test`) so the signup webhook actually fires every time — orphans accumulate in dev02 / CI at ~1 per OWUI version bump, tolerable without a delete hook. Add `tests/auth/helpers/owui-admin.ts` later if hygiene matters. Wire as part of the OWUI version-bump checklist (Renovate PR is the trigger per ADR 0015). Complement (not replace) with a Grafana alert on `scout_report_viewer_owui_webhook_events_total{result="error"}` — cheap and catches loud regressions; alert misses silent ones (flag rename → we write a wrong key with `result="enabled"`), which is exactly what the Playwright test plugs.

