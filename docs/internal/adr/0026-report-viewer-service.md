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
report-viewer-service API endpoints.

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
  TanStack Query + React Router + `oidc-client-ts`. Built as static files via
  Vite, bundled into the Python container, served by FastAPI's `StaticFiles`
  at `/`. No Node runtime in production.
- **Deployment**: Single Kubernetes deployment in `scout-analytics`. One
  ingress, one Helm chart, one Keycloak client (`report_viewer_app`).

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
(`https://chat.<env>/spa/searches/{id}` proxied to report-viewer-service).
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

- **Postgres `searches` table** holds: `id`, `owner_sub`, `kind`,
  `id_column`, `source_sql`, `sql_explanation`, `highlight_terms`,
  `parent_id` (informational lineage only — has no SQL impact),
  `count` (cached at create time via one `SELECT COUNT(*) FROM
  (<source_sql>)`), `owui_chat_id`, `owui_chat_title`, timestamps.
- **No Delta side-table**, no Postgres `id_list`, no row_metadata blob.
  Every read wraps `source_sql` as a subquery and applies
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

- The SPA holds the user's Keycloak access token directly via
  `oidc-client-ts`, minted by the new `datasets_app` Keycloak client (with
  `trino-audience` in `defaultClientScopes`, PKCE on).
- The iframe uses **silent SSO** (`signinSilent` against Keycloak's
  authentication endpoint with `prompt=none`) to pick up Keycloak's existing
  SSO session without a full-window redirect.
- API calls go out as `Authorization: Bearer <token>`. FastAPI's existing
  Path 1 (Bearer JWT, JWKS-validated) handles authentication; the same
  Bearer forwards to Trino per
  [ADR 0022](0022-trino-auth-and-impersonation.md)'s JupyterHub-style JWT
  pass-through pattern.
- oauth2-proxy stays at the ingress for the approval gate
  ([ADR 0003](0003-oauth2-proxy.md)) but is **not** part of the token-
  forwarding chain. No `pass_access_token`, no `X-Auth-Request-Access-Token`
  in the forwardAuth middleware, no `trino-audience` on the oauth2-proxy
  Keycloak client.

### REST API endpoints

The REST API is the underlying service contract. It's resource-shaped (CRUD
on searches plus RPC operations on reports), independent of the
intent-shaped tool surface the LLM sees. Multiple tool consumers (OWUI today,
MCP later) all hit this same API.

**Search CRUD** (the search resource):

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/searches` | Save a SQL-defined search. Body: `{ sql, sql_explanation, highlight_terms, owui_chat_id/title }`. The service stores the SQL, runs `SELECT COUNT(*) FROM (<sql>) sub` to cache the count, and returns `id + count + sample + summary + view_url`. The CSV-upload path uses a sibling endpoint `POST /api/searches/from-file` that validates the supplied identifiers against `reports_latest` and saves a `WHERE <id_col> IN (...)` SQL — same downstream shape. No materialization in either case. |
| GET | `/api/searches/{id}` | Search metadata (count, owner, timestamps). |
| GET | `/api/searches/{id}/rows?page&limit&sort&filter` | Paginated rows for the SPA table. Server-side filter and sort on Trino-native columns. |
| GET | `/api/searches/{id}/csv` | Streaming chunked CSV download (mirrors `/rows` — paginated JSON vs. streamed CSV). |
| GET | `/api/searches/{id}/accessions` | Distinct accession-number list for the Send-to-XNAT handoff. |
| DELETE | `/api/searches/{id}` | Explicit search deletion ahead of TTL. |

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

To avoid that, report-viewer-service exposes `POST /webhooks/owui-new-user`
as the receiver for OWUI's admin signup webhook (configured via
OWUI's `WEBHOOK_URL` PersistentConfig field). On a signup event the
receiver does one Postgres statement against OWUI's own database
that merge-sets both flags to true on the new user's `settings` JSON
column. Best-effort: errors are logged + recorded as a metric but
not raised, so OWUI's webhook retry loop doesn't hammer transient
failures.

**Why direct DB, not OWUI's HTTP API?** OWUI 0.9.6 has no admin
endpoint to write another user's UI settings:

- `POST /api/v1/users/{user_id}/update` (admin-only) accepts only
  `role`, `name`, `email`, `profile_image_url`, `password` — see
  `UserUpdateForm` in `backend/open_webui/models/users.py`.
- `POST /api/v1/users/user/settings/update` writes the *session
  user's own id* regardless of admin role.

Upstream [open-webui/open-webui#20770](https://github.com/open-webui/open-webui/pull/20770)
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

1. **OWUI Postgres connection** — report-viewer-service mounts a
   `REPORT_VIEWER_OWUI_DATABASE_URL` env via `secretKeyRef` from
   `open-webui-secrets`, key `DATABASE_URL` (OWUI's own Postgres
   credential, reused for now — see the least-privileged-role
   follow-up below).

2. **OWUI admin-settings `WEBHOOK_URL`** — set to
   `http://report-viewer-service.<chatbot_namespace>:8000/webhooks/owui-new-user`.
   PersistentConfig value pushed by the open-webui-bootstrap chart on
   every install-chat. Note that OWUI's `validate_url()` SSRF guard
   rejects RFC1918 hostnames by default — we set
   `ENABLE_RAG_LOCAL_WEB_FETCH=true` on the OWUI deployment per
   upstream [#24587](https://github.com/open-webui/open-webui/pull/24587)
   to allow the in-cluster Service URL.

3. **(Optional but recommended)** `REPORT_VIEWER_OWUI_WEBHOOK_SECRET`
   env on report-viewer-service + matching `X-Scout-Webhook-Secret`
   header on OWUI's webhook config so forged requests can't seed
   settings for arbitrary users. NetworkPolicy on report-viewer-service
   already gates intra-cluster ingress; this is defense-in-depth.

Until #1 lands, the receiver path is reachable but returns 503 ("OWUI
database URL not configured") and no settings are written.

**Follow-up — least-privileged Postgres role.** v1 reuses OWUI's
own Postgres credential (which has full table access). Future
hardening: create a dedicated role
`CREATE ROLE rvs_owui_settings_writer; GRANT UPDATE (settings) ON "user" TO rvs_owui_settings_writer;`
add a new secret key with that role's URL, and point
report-viewer-service's `secretEnv` at it instead of `DATABASE_URL`.

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
  stamped with contextual fields (search id, count, kind, owner) so log lines
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

- SPA emits parent.postMessage({type:'iframe:height', height: N}, '*') for height instead for better cross-origin support.

- Test aggregate queries and non-cohort queries from the LLM.
  - What is the /aggregate endpoint??

- Review the scout query OWUI tool implemntation.

- Review React App SPA implementaition.

- Prompt review needed, too much irrelevant stuff has built up while iterating on the service. 

- Report snippets are not being given to the llm for context and the diagnosis codes are pressented outside of the results table given to the llm. This is a regression from the POC, the llm needs some report context.

- Report loading is slow (a few seconds) on an individual report. This was faster and should be for a single report.

- When clicking on a report for details, split left and right panes to make better use of space. Patient info and report metadata on the left, full report text on the right.

- Text moves under the "Filter..." row as you scroll and is not hidden behind it. The filter row is sticky but the text is apperaing between the filter row and the header row.

- The LLM is picking too many highlight terms, maybe limite to 3 - 5.

- Update code from dataservice to report-viewer-service, including the new SPA and API endpoints and the OWUI integration points.

- Update observability stack

-  PHI in searches.source_sql — imported CSV cohorts persist WHERE epic_mrn IN ('123',...) clear-text in Postgres. Retention/encryption decision needed.

- **Auth hardening pass.** Issues found on review. Pick **either** option A or option B for issue #1 — they are mutually exclusive approaches to the same problem. Issues #2 and #3 apply regardless of which option is chosen.

  1. **Keycloak access token forwarding to the service.** `ansible/roles/oauth2-proxy/tasks/deploy.yaml:98-100` adds `X-Auth-Request-Access-Token` to the shared `oauth2-proxy-auth` middleware — fans the raw user JWT out to every service annotated with that middleware (temporal, open-webui, grafana, minio, launchpad, voila, superset, jupyter, report-viewer-service). Only report-viewer-service has a documented reason to consume it. Combined with `trino-audience` now in the oauth2-proxy client's `defaultClientScopes` (`ansible/roles/keycloak/templates/scout-realm.json.j2:280`), anything that captures the cookie can call Trino as the user. This pattern ("oauth2-proxy as token broker") is novel — no other Scout service uses it, and it conflicts with ADR 0003's framing of oauth2-proxy as approval-gate only.

     **Option A — Contain the current shape (dedicated middleware).** Keep the cookie-based browser flow; restrict the leak.
     - Split out a dedicated `oauth2-proxy-auth-trino` middleware that includes the token header; revert the shared middleware to headers only; reference the new middleware only from `ansible/roles/report_viewer_service/templates/values.yaml.j2`.
     - Defense-in-depth: move `trino-audience` to `optionalClientScopes` on the oauth2-proxy client and have oauth2-proxy request it explicitly via its `scope` config (`trino-audience` on the open-webui client stays in defaults since OWUI can't toggle scope per-tool).
     - Pros: smaller diff, no SPA changes, no new Keycloak client.
     - Cons: leaves a brand-new auth pattern in Scout's catalog. Service keeps 3 inbound auth paths (Bearer / oauth2-proxy headers / dev secret). Future services may copy the pattern. ADR 0003 and ADR 0026's own Authentication section (lines 226-243) need amendments justifying the new pattern.

     **Option B — Unify on Bearer JWT (oidc-client-ts in the SPA).** Match Jupyter's pattern; collapse browser + OWUI tool onto one auth path. This is what ADR 0026's Authentication section actually describes today.
     - Add a new public Keycloak client `report_viewer_app` (PKCE, no client_secret) with `trino-audience` in `defaultClientScopes` and redirect URIs for both the rvs host and the chat-host alias.
     - Bootstrap `oidc-client-ts` in `frontend/src/main.tsx` before React renders; use silent SSO (`signinSilent` with `prompt=none`) so the browser inherits the existing Keycloak SSO session with no extra login.
     - SPA sends `Authorization: Bearer <jwt>` on every fetch; `auth.py` Path 1 (existing) handles it.
     - Revert the branch's oauth2-proxy changes: drop `pass_access_token`, `cookie_refresh`, and `X-Auth-Request-Access-Token` from the middleware; drop `trino-audience` from the oauth2-proxy Keycloak client.
     - Service's `auth.py` collapses to one real path (Bearer JWT); Path 2 (oauth2-proxy headers) can be removed or kept as a thin fallback.
     - Pros: one auth pattern in the service (Bearer JWT), matches Jupyter and ADR 0022 exactly. oauth2-proxy reverts to its ADR 0003 shape (approval gate, headers only). OWUI tool path unchanged. No new pattern in the Scout catalog. ADR 0026's Authentication section becomes accurate without edits.
     - Cons: larger diff (new client in realm template, new bootstrap in SPA, error handling for silent-SSO failure → full-window redirect). One new public Keycloak client (Scout's first public client; the existing user-facing clients are confidential).

  2. **Owner-or-admin scope on search endpoints.** `routes/searches.py:605, 693, 753, 818` (the `/rows`, `/reports/{id}`, `/accessions`, `/export.csv` handlers) pass `owner_sub=None` to `store.get_search`, so anyone authenticated with the search ID gets the saved SQL back. OPA still clamps row-level data per-requester, but `source_sql` itself can carry PHI (file-imported `WHERE epic_mrn IN (...)`) and the metadata response leaks regardless. Comment in `frontend/src/api/client.ts:78-83` rationalizes this as "iframe context can't always resolve user identity" — false; the chat-host alias goes through the same oauth2-proxy middleware.

     Fix: add `groups` to the `User` dataclass (Path 1: from JWT claims; Path 2 if still present: from the forwarded access token or an added `X-Auth-Request-Groups` header). Replace `owner_sub=None` with a helper that returns 404 unless `user.sub == owner_sub` OR `"scout-admin" in user.groups`. Same group name as ADR 0020's hardcoded `scout-admin` so admin status is consistent across services. 404 (not 403) so search IDs don't become an existence oracle.

  3. **Search ID entropy.** `ids.py:14` generates 6 base62 chars and the comment claims "56 bits" — actual entropy is `log2(62^6) ≈ 35.7 bits`, brute-forceable. After fix #2 this is owner-scoped so the attack window is narrower, but it's a cheap fix on top: bump to ~16 chars (~95 bits) and correct the comment math.

- Test CSV Upload and Download.

- Improve "Explain SQL" UI / UX
  - The sql is pretty ugly to read
  - Add a copy to clipboard button for the sql code.

- When reviewing a row / report add a button that says "Discuss Report with Chat" (or something like that) that tells the LLM to pull that report into context and discuss to with the user. Make sure to use the file location of the report in the lake as the identifier when pulling the report into context.

- Age filter takes just a single number right now, needs to accept a range.

- Send to XNAT handoff with IQ plugin. Not needed for the initial release but will be needed soon after.

- **Auto-set per-user `iframeSandboxAllowSameOrigin` (and `iframeSandboxAllowForms`) on new OWUI signups.** Required so the in-chat iframe viewer renders with `allow-same-origin` from a new user's very first search, without making them dig into Settings > Interface > Artifacts and toggle it manually. OWUI 0.9.6 has no admin-global default for these flags and no admin API to write another user's UI settings (`UserUpdateForm` only accepts `role`/`name`/`email`/`profile_image_url`/`password`; `/user/settings/update` writes the session user's own id, ignoring admin role). Upstream PR open-webui/open-webui#20770 would add the missing admin endpoint but per the contributor "probably won't get merged anytime soon."

  Path picked: direct write into the OWUI Postgres `"user".settings` JSON column from the existing signup-webhook receiver, scoped via a least-privileged Postgres role granted only `UPDATE (settings) ON "user"`. The webhook is `await`ed inside OWUI's OAuth callback (`oauth.py` ~1745 in 0.9.6), so the settings stamp completes before OWUI redirects the browser → first page-load already sees the corrected flags → no first-iframe-needs-reload race.

  Eliminated alternatives (documented so we don't re-litigate):
  - Image fork of OWUI with PR #20770 baked in — perpetual maintenance burden until upstream merges
  - InitContainer overlay that appends the PR snippet to `users.py` at pod start — brittle to upstream router-file shape changes
  - In-tool update from `scout_report_viewer_tool.py` calling `Users.update_user_settings_by_id` — has a race (server-side write doesn't push to client's settings cache; first iframe renders with stale sandbox attr, user has to reload once)
  - OWUI admin webhook for new-user notification with `WEBHOOK_URL` — fires correctly post-`ENABLE_RAG_LOCAL_WEB_FETCH=true` and post-receiver-fix, but only gives us a *trigger*; we still need a way to actually write the setting, and OWUI's API doesn't expose one

  Note: the receiver already exists at `routes/owui_webhook.py` with the trigger wired. Earlier work in this session built it around a fictional "auto-enable" role-flip — replace that body with `_apply_iframe_defaults(user_id)` doing the one-row `jsonb_set` UPDATE.

  Earlier sections of this ADR's "OWUI new-user auto-enable" framing were misleading — it was never about flipping `pending → enabled` (users are already auto-enabled in the current realm config) — it was always about getting these UI flags set so the iframe works out of the box.

