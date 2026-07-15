# ADR 0029: Report Viewer Service for Chat

**Date**: 2026-07-07
**Status**: Accepted
**Decision Owner**: TAG Team
**Supersedes**: Open WebUI's Trino-access parts of ADR 0019, ADR 0020, and ADR 0022 (the MCP Trino tool, openwebui_mcp_svc, and the interim PASSWORD authenticator)

## Context

Scout's chat surface (Open WebUI) is an effective natural-language entry point for cohort building. Researchers describe what they want, an LLM translates it to Trino SQL against the Delta Lake radiology reports, and a cohort emerges through follow-up questions. The chat context is not designed for large-data interaction though. Dumping thousands of rows into the LLM's window blows its context budget and produces worse answers on the next turn. Rendering the same rows as chat markdown gives researchers a text surface but no browse, sort, filter, or export affordances, and it still puts the payload into the model's context.

The gap this ADR addresses is the space between the LLM's ability to generate SQL for large cohorts and the researcher's need to view, refine, and export those cohorts in a usable way.

## Decision

Build **report-viewer** as a Scout-owned microservice that lives next to chat. The backend is FastAPI, and the React + Vite + TypeScript + Tailwind SPA is bundled as static files into the Python container at CI time. The service ships as one Kubernetes Deployment in `scout-analytics`, one Ingress, one Helm chart, and one Keycloak client (`report_viewer_svc`). There is no Node runtime in production and no separate frontend service.

### Tool surface (LLM-facing)

Three intent-shaped Open WebUI native tools, each a thin wrapper over a REST endpoint:

| Tool | Endpoint | Persists? |
|---|---|---|
| `scout_find_reports` | `POST /api/searches` | Yes, as a saved search |
| `scout_get_reports` | `POST /api/reports/read` | No, transient |
| `scout_query_sql` | `POST /api/reports/query` | No, transient |

`scout_find_reports` accepts SQL plus metadata (`sql_explanation`, `match_terms`, `match_diagnoses`) emitted by the LLM, saves it as a named search, and returns a sample of rows plus evidence context. The resulting search is browseable in an iframe above the LLM reply and supports CSV export. The other two tools execute Trino reads and return rows directly, without persisting a search row or rendering an iframe.

Splitting the surface this way keeps the intent explicit for the LLM: cohort building persists, lookup and analytics do not. Collapsing all three into one endpoint would either force persistence on every operation or make the LLM signal intent through arguments the researcher never sees.

### Just-in-time cohort evaluation

A search is a saved SQL query plus minimal metadata. Nothing about which rows match is stored. Every read wraps the saved `sql` as a subquery and applies pagination, sort, and filter at the Trino layer. The `searches` table holds `id`, `owner_sub`, `id_column`, `sql`, `sql_explanation`, `match_terms`, `match_diagnoses`, `row_count` (cached at create time via one `SELECT COUNT(*)`), `owui_chat_id`, and `created_at`. Schema migrations use [yoyo](https://ollycope.com/software/yoyo/), a standalone Python migration tool; `db.py` applies the versioned SQL files in `migrations/` on startup. Query refinement is not tracked with a `parent_id` column. When a user narrows a cohort, the LLM is instructed to re-emit full SQL for the new search and the old search is unchanged. 

### Required projections

`primary_report_identifier` and `accession_number` are required projections on every search. Both are needed by the SPA table and the future XNAT handoff need. Making them required at save time avoids the failure mode where a search is persisted but useless to downstream consumers.

### Interpretable SQL

The LLM-emitted SQL is a researcher-facing artifact, not an internal detail. `sql` and `sql_explanation` are persisted side by side and the SPA renders them together in an "explain filters" view. Cohort clinical filters (boolean and synonym alternation, sentence-bounded negation removal, ICD-code arm union) follow the patterns documented in `scout-system-prompt.md`. The system prompt instructs the LLM to accept natural-language refinement directives that toggle the corresponding clauses in the re-emitted SQL.

### Authentication

There are two inbound callers and one outbound Trino pattern.

**Inbound.** Two paths, because the browser has no JWT to present.

- **Rendering results (`user → browser SPA/iframe → Traefik → report-viewer`).** At the ingress, Traefik runs oauth2-proxy as forwardAuth (the ADR 0003 approval gate): it validates the session, and Traefik forwards the request with the username in `X-Auth-Request-Preferred-Username` plus the `X-Report-Viewer-Gateway` secret. No token reaches report-viewer.
- **Running a search / read (`user's chat prompt → LLM → OWUI tool → report-viewer`).** The tool carries the user's Keycloak access token as `Authorization: Bearer <__oauth_token__>`. FastAPI validates it against Keycloak JWKS (RS256-only allowlist, signature, `exp`, `iss`, `aud=report-viewer`).

Both resolve to the same `User`, but are trusted differently. The bearer is cryptographically verified against Keycloak's JWKS, so a forged or tampered token is rejected no matter which pod sends it. The header can't be verified. `X-Auth-Request-Preferred-Username` is plaintext, only as trustworthy as its sender, and NetworkPolicy lets the OWUI and Prometheus pods reach `/api` directly, so either could send a forged username with no token (OWUI most of all, since it can run tool-driven server-side code). report-viewer therefore honors the header only when the request also carries `X-Report-Viewer-Gateway`, a secret Traefik injects at the ingress. A pod can't obtain that secret without passing through oauth2-proxy, which overwrites the username with the caller's real identity, so no caller can hold both a forged name and the secret. NetworkPolicy is defense-in-depth, not the gate.

**Outbound.** Report-viewer follows the impersonation pattern from ADR 0022, same as Superset and Voila. It authenticates to Trino as `report_viewer_svc` (a confidential Keycloak client with `trino-audience` in `defaultClientScopes`), mints a Bearer via `client_credentials`, caches it in-process, and refreshes when ⅕ of the lifetime remains (ADR 0024 ratio, single-flight under a lock). Every Trino call goes out as `Authorization: Bearer <svc-token>` plus `X-Trino-User: <user.sub>`. OPA's service-principal set grants `report_viewer_svc` `ImpersonateUser`; the per-user row filters and column masks evaluate against the impersonated identity. JWT pass-through is not used because report-viewer has no fresh-user-token custodian; impersonation is the established fallback for services in that position.

**Trino auth mode.** Open WebUI no longer calls Trino directly, so the interim `PASSWORD` authenticator from ADR 0022 (`openwebui_mcp_svc`, `password.db.j2`, `authenticationType: PASSWORD,JWT`) is removed. Trino runs JWT-only. Every client, including report-viewer, authenticates with JWT.

**Flows.** Both inbound paths converge on the same `report_viewer_svc` JWT + `X-Trino-User` impersonation call to Trino.

```
report-viewer, browser SPA / iframe (JWT + impersonation, header-driven)
────────────────────────────────────────────────────────────────────────────────
  user → browser SPA/iframe → Traefik (ingress)
       ↓ oauth2-proxy forwardAuth validates session (set_xauthrequest=true)
       ↓ X-Auth-Request-Preferred-Username: <username>   (username only; no token forwarded)
       ↓ X-Report-Viewer-Gateway: <secret>               (injected by Traefik; gates the header path)
  report-viewer pod
       ↓ verifies gateway secret, reads username header → user context
       ↓ trino_client mints report_viewer_svc JWT (client_credentials; aud=trino; cached, re-minted at 4/5 lifetime)
       ↓ HTTP: Bearer <report_viewer_svc JWT>, X-Trino-User: <user>
  Trino: principal=report_viewer_svc, user=alice → OPA evaluates against data.users["alice"]
                                       impersonation: report_viewer_svc ∈ trino_service_principals

report-viewer, OWUI tool runtime (JWT + impersonation, Bearer inbound)
──────────────────────────────────────────────────────────────────────────────
  user chats in OWUI → tool runtime POSTs to report-viewer in-cluster
       ↓ Authorization: Bearer <user-jwt>                # user JWT minted by `open-webui` client; aud=report-viewer
  report-viewer: validates inbound JWT vs Keycloak JWKS (signature, iss, exp, aud=report-viewer)
       ↓ reads preferred_username from validated token; JWT discarded (never forwarded)
       ↓ trino_client mints report_viewer_svc JWT (client_credentials; aud=trino; cached, re-minted at 4/5 lifetime)
       ↓ HTTP: Bearer <report_viewer_svc JWT>, X-Trino-User: <user>
  Trino: principal=report_viewer_svc, user=alice → OPA evaluates against data.users["alice"]
                                       impersonation: report_viewer_svc ∈ trino_service_principals
```

### iframe embedding

The SPA is served from its own subdomain (default `report-viewer.<domain>`) and is loaded cross-origin from the chat host. The iframe URL is `https://{report_viewer_host}/spa/searches/{id}`, and OWUI embeds it via its `message.embeds` mechanism. Height sync between the SPA and the chat frame uses `postMessage` targeted at the chat origin, since `window.frameElement` is not readable cross-origin.

OWUI's `message.embeds` iframe sandbox must include `allow-same-origin` and `allow-forms`. `allow-same-origin` is required so the iframe keeps its own origin identity rather than loading as a unique opaque origin. Without it, the SPA cannot use `document.cookie` or storage APIs, and `fetch('/api/...')` on its own backend cannot send the oauth2-proxy session cookie that authenticates the request. `allow-forms` covers SPA form submissions.

In OWUI 0.9.6 these flags are per-user settings with no admin-global override, and both default to `false`, so every new researcher would hit a broken iframe until they toggled the flags manually. To avoid that, report-viewer receives an OWUI signup webhook and seeds the flags on the new user's `settings` row before their first hydrate (see below).

### OWUI new-user webhook

`POST /webhooks/owui-new-user` receives OWUI's admin signup webhook (configured via OWUI's `WEBHOOK_URL` field on install-chat). On a signup event the receiver runs one idempotent `UPDATE "user" SET settings = jsonb_set(...)` against OWUI's Postgres to set `iframeSandboxAllowSameOrigin` and `iframeSandboxAllowForms` to `true`.

**Why direct DB, not OWUI's HTTP API.** OWUI 0.9.6 has no admin endpoint to write another user's UI settings. Upstream open-webui#20770 would add one, but the contributor said it "probably won't get merged anytime soon." Direct DB is the only path that lands the setting before the user's first page hydrate.

**Postgres role.** The receiver connects as a dedicated `owui_settings_writer` role granted only `SELECT (id, settings)` and `UPDATE (settings)` on `"user"`. The role is provisioned by the `open-webui` Ansible role so it stays in sync with the OWUI database lifecycle. The connection URL is rendered into report-viewer's Helm Secret from inventory vars.

**Trust model.** The receiver has no application-layer authentication. OWUI's `POST /api/webhook` admin endpoint accepts only `{"url": "..."}` with no headers, no signing key, and no caller identity, so no shared-secret or service-principal pattern is wireable on the OWUI side without forking OWUI. Protection is three layers:

1. **Ingress path enumeration.** Only `/api/*` and `/spa/*` are exposed. `/webhooks/*`, `/metrics`, and `/healthz` are not in the Ingress and return 404 externally.
2. **NetworkPolicy.** In-cluster ingress to port 8000 is restricted to Traefik and OWUI peers, plus Prometheus for scraping. Same precedent as `trino-rw` (ADR 0019), which gates its in-cluster surface without an app-layer credential.
3. **Operation narrowness.** The receiver sets two boolean UI flags to `true`. The response is 204 whether the user exists, does not exist, or already has the flags set, so there is no oracle to probe and no data leaked. SQL is parameterized.

**OWUI 0.10.x.** OWUI 0.10 upstream reworks the events and webhooks surface. When Scout picks up 0.10, this webhook path is a natural candidate to move onto the new tooling, and the direct-DB write may become unnecessary if the admin settings endpoint from #20770 (or a successor) lands.

### Observability

Prometheus scrapes `/metrics` via `prometheus-fastapi-instrumentator`, plus domain counters and histograms for search creation, Trino op latency, Postgres op latency, search size, and webhook events. Logs are structured JSON to stdout and picked up by Loki.

## Consequences

- Report-viewer is default-on. The `enable_report_viewer` feature flag was removed.
- Every `/rows` page and CSV export costs a Trino scan of the saved SQL, so cohort browsing latency tracks Trino query performance directly.
- Refinement has no cross-cohort SQL dependency. The LLM must keep original conditions intact when adding a filter; the system prompt enforces this with examples.
- The OWUI iframe experience depends on the sandbox flags being seeded at signup. If the webhook is unreachable or the DB URL is unset, new users will hit a broken iframe.

## Alternatives Considered

| Option | Verdict |
|--------|---------|
| **Embedded iframe SPA (selected)** | Cohort rows stay out of the LLM's context, the SPA gives a full browse, sort, filter, and export surface, and the service ships as one deployment behind one ingress |
| Render cohort rows as chat markdown | Rejected: no browse, sort, filter, or export affordances, and cohort rows still land in the LLM's context |
| OWUI Rich UI / Artifacts panel | Rejected: data handling inside the OWUI container drove pod restarts and high CPU and memory usage, and OWUI's follow-up-suggestion and chat-title LLM calls still pulled iframe content into the model's context |
| Materialize cohort rows into a Delta side-table | Rejected: dual-write path adds real complexity, and every row of every saved search would need to be materialized and stored |

## Future work

**XNAT handoff.** Submission from an accession set into an XNAT project is deferred with the XNAT deployment work (ADR 0026).

**OWUI 0.10.x migration.** When Scout picks up OWUI 0.10, the events and webhooks surface changes. Move the new-user hook onto the new tooling, and if #20770 or a successor admin settings endpoint lands, retire the direct-DB write.

## Open Questions

- [ ] OWUI 0.10.x migration, and whether the direct-DB webhook path can retire.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| OWUI upstream changes the signup webhook contract or the settings JSONB schema | Medium | High (new users hit a broken iframe) | The least-privilege Postgres role limits blast radius, and the 0.10.x migration is on the roadmap |
| Trino latency on saved-SQL scans degrades with data growth | Medium | Medium | Read-path cache or per-cohort materialization can be added without an API change |
| `report_viewer_svc` token compromised in-cluster | Low | Medium | NetworkPolicy restricts Bearer-bearing traffic to OWUI pods; the token grants `ImpersonateUser` only, so per-user OPA row filters and column masks still apply to the impersonated identity |

## Retiring the MCP Trino tool

Open WebUI previously reached Trino through the upstream `tuannvm/mcp-trino` server. This change removes it from the Ansible roles, but removing a task does not delete resources already running in a cluster, so an in-place upgrade needs manual cleanup:

```
helm uninstall mcp-trino -n scout-analytics
kubectl delete networkpolicy mcp-trino-ingress -n scout-analytics
kubectl delete secret trino-password-auth -n scout-analytics
```

The Keycloak `openwebui_mcp_svc` client, the `mcp-trino-audience` scope, and the OWUI `scout-db` tool-server registration are reconciled away on redeploy (keycloak-config-cli is full-managed, and the OWUI config push is declarative). Drop the `keycloak_openwebui_mcp_svc_client_secret` and `trino_openwebui_mcp_svc_password` inventory keys.

## References

- [ADR 0003: OAuth2 Proxy as Authentication Middleware](0003-oauth2-proxy-authentication-middleware.md)
- [ADR 0019: Read-Write Trino Instance for Transformer-Issued View DDL](0019-trino-rw-instance-for-views.md)
- [ADR 0020: Trino Authorization via OPA with Keycloak Attributes](0020-trino-authz-architecture.md)
- [ADR 0022: Trino Authentication and Identity Propagation](0022-trino-auth-and-impersonation.md)
- [ADR 0024: Token Refresh for SDK Trino Access](0024-sdk-trino-token-refresh.md)
- [ADR 0026: XNAT Deployment Posture and Lifecycle](0026-xnat-deployment-posture-and-lifecycle.md)