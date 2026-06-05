# Datasets Service — Design Notes (Draft)

> **Status:** Exploratory. This is a thinking document, not an accepted decision.
> It deliberately leaves open questions unanswered and flags them as future
> investigations. When the shape stabilizes, the durable decisions should be
> promoted to an ADR.

## Motivation

Scout's chat experience (Open WebUI) lets users explore the Delta Lake by asking
questions that compile to Trino SQL. That works well for small, summarizable
answers. It breaks down badly the moment a query returns a lot of rows:

- **Context blow-up.** A query can easily return ~10K rows, and nothing stops a
  user from asking for something that scans the full ~2M-row dataset. Feeding
  those rows back through the LLM is slow, expensive, destroys the context
  window, and degrades answer quality — the model ends up reasoning over a
  truncated, randomly-ordered row dump instead of the question.
- **Wrong unit of work.** What users actually want from a large result is rarely
  "read every row in chat." It's "turn this query into a **cohort** — a named,
  reusable set of records — and hand it to another service for downstream
  processing." The rows are an implementation detail of that cohort, not the
  deliverable.

The **Datasets service** exists to make the cohort — not the row dump — the
first-class object. The guiding principle:

> **A cohort is a server-side query specification, not a pile of rows in the
> browser or in the LLM's context.**

Keeping the materialized result out of three places — the LLM context, the
browser's memory, and any tool's return value — is the same discipline applied
consistently. The Datasets service is where that discipline lives.

## What it needs to do

A small backend service (plus a thin UI surface) that owns the lifecycle of a
cohort. Sketch of responsibilities:

1. **Persist cohort definitions**, not row sets. A cohort is roughly:

   ```
   cohort_id
   owner                # whose identity defines the cohort (see Auth)
   base_query / predicate
   extra_filters[]      # refinements layered on the base query
   include_ids[]        # explicit hand-added exceptions
   exclude_ids[]        # explicit hand-removed exceptions
   count                # cached, derived
   created_at / updated_at
   ```

   The effective cohort is `predicate − exclude_ids + include_ids`. Storing a
   predicate (plus a small exception delta) is what lets this scale to millions
   of rows without ever materializing an id list of that size.

2. **Serve authenticated, paginated views** over a cohort by re-issuing queries
   to Trino:
   - a **page of rows** (`offset/limit`) for the table preview,
   - **facet counts / distributions** for filter chips,
   - **summary statistics** and a small **sample** for the LLM to reason over,
   - the **running count** of the current cohort definition.

   The service never returns the whole cohort to anyone. Callers get pages,
   facets, and summaries.

3. **Apply refinements.** Accept filter/predicate edits (from either the UI or
   the LLM — see below), update the cohort definition, and recompute the count.

4. **Hand off to downstream services.** Once a cohort is finalized it gets sent
   to other Scout services for processing. The handoff is by **reference**
   (`cohort_id` / a materialized table reference), never by shipping rows. The
   mechanics of downstream processing are out of scope for this doc.

What it explicitly should **not** do: load full result sets into the chat, the
browser, or a tool response.

## How it interacts with chat

There are two clients, and the central design requirement is that **they edit
the same cohort definition** so they never diverge.

```
                 ┌─────────────────────────────────────────┐
                 │              Open WebUI                   │
                 │                                           │
   user chat ───▶│  LLM tool (server-side)                   │──┐
                 │   - create cohort from a query            │  │  authenticated
                 │   - query/summarize an existing cohort    │  │  views, keyed
                 │                                           │  │  by cohort_id
                 │  iframe embed (browser)                   │──┤
                 │   - table preview (paginated)             │  │
                 │   - filter chips / facets                 │  │
                 │   - running count, "finalize" action      │  │
                 └─────────────────────────────────────────┘  │
                                                               ▼
                                              ┌─────────────────────────────┐
                                              │      Datasets service        │
                                              │  cohort defs + Trino queries │
                                              └─────────────────────────────┘
                                                               │ queries as the user
                                                               ▼
                                                        Trino → Delta Lake
                                                        (OPA row filters apply)
```

### The LLM tool path (server-side)

- The **create-cohort tool** takes the user's query, persists it as a cohort
  definition, and returns to the model only a **summary** (count, a few facet
  distributions, maybe a tiny sample) plus the `cohort_id`. The rows stay in the
  lake. This is what keeps the result out of context.
- A **query-cohort tool** takes a `cohort_id` and answers follow-up questions by
  running **aggregate/sample** queries against the cohort — counts, group-bys, a
  bounded sample — never the full content. "Chat about my results" becomes "ask
  questions that compile to SQL over the saved cohort." This is how we avoid the
  trap of a tool that reads the attached file and re-injects 10K rows.
- Because the LLM edits the predicate through the same cohort definition, any
  refinement it makes ("narrow to CT studies in 2023") is immediately reflected
  in the UI's table and count, and vice-versa.

### The iframe path (browser)

- The embed renders a **table preview + filter chips + running count**. It holds
  **one page** of rows at a time, fetched on demand from the Datasets service —
  never the whole cohort. This keeps the browser responsive whether the cohort
  is 10K or 2M rows, and keeps multiple cohorts on one page cheap.
- Filtering is **predicate-first**, not row-by-row: facet chips that map to SQL
  `WHERE`, "select all N matching" semantics, and a small include/exclude
  exception set for manual tweaks. Per-row checkbox selection does not scale past
  a few hundred rows and should not be the primary mechanism.
- When the user edits filters, the iframe persists the change to the cohort
  definition (so the LLM sees it too) and re-reads the page + count.

### NL refinement vs. direct manipulation

Chat-driven refinement and UI filter chips are **two editors over one cohort
spec**, not competing features. NL is good for fuzzy/semantic narrowing; direct
chips are good for "I can see that facet, let me click it." The only failure mode
is when the two hold separate state — which the shared cohort definition is
specifically designed to prevent.

## Auth (the part that needs the most care)

Both clients must resolve to **the same user identity**, and — critically — the
Datasets service must query Trino **as that user**, so OPA's per-user row filters
and column masks (ADR 0020 / 0023) still apply. If the service ever queries the
lake as an unscoped service principal, **it becomes a row-filter bypass** and the
cohort could contain rows the user is not permitted to see. That is the single
most important auth invariant here.

### Browser iframe → Datasets service

The promising approach: serve the Datasets UI from **its own Scout subdomain
behind OAuth2 Proxy + Keycloak** (consistent with ADR 0003) and embed it as a
**URL embed**. The browser then already holds the user's SSO session for that
origin, so the iframe's requests are authenticated automatically — no token has
to be smuggled across the OWUI iframe sandbox.

**Open questions / future investigation:**

- **Third-party cookie / `SameSite` behavior.** The Datasets UI is embedded as a
  cross-origin iframe inside the Open WebUI page. Session cookies will be treated
  as third-party; browsers increasingly block these unless `SameSite=None;
  Secure` (and even then, partitioning rules like CHIPS may apply). Need to
  verify the OAuth2 Proxy session cookie survives inside the embed, or choose a
  same-site subdomain strategy / a token-handoff alternative.
- **CSP `connect-src` / `frame-src`.** Scout's hardened CSP (ADR 0009 / 0012)
  must explicitly allow the Datasets origin for both the frame and any `fetch()`
  the iframe makes back to the service.

### LLM tool (server-side) → Datasets service

The tool runs inside Open WebUI as a service principal but must act **on behalf
of the calling user**. The natural pattern to reuse is the one Open WebUI's MCP
client already uses for Trino: authenticate as a dedicated Keycloak service
principal and assert the end user via an impersonation header (ADR 0022's
`X-Trino-User` model). The Datasets service would validate the service principal,
trust the asserted user, and then issue its Trino queries under that user's
identity.

**Open questions / future investigation:**

- **Impersonation vs. JWT pass-through.** Can an Open WebUI tool obtain the
  user's own Keycloak token (e.g. via `auth_state`, as JupyterHub does in ADR
  0022) and pass it through, instead of impersonating via a service principal?
  Pass-through is cleaner because it keeps a single identity all the way to OPA;
  impersonation requires the Datasets service to be a trusted asserting party.
- **A dedicated service principal** (`datasets_svc`?) and its token lifespan /
  refresh story, paralleling ADR 0024. Long-lived cohort-building sessions will
  outlive a single short access token.
- **How the Datasets service queries Trino as the user.** JWT pass-through to
  `trino-analytics`, or impersonation — and how that composes with the
  service-to-service hop above without losing the user identity that OPA needs.

### Identity-relative cohorts

Because lake contents are user-filtered, **a cohort is user-relative, not
absolute.** This has a conceptual consequence for the downstream handoff that
must be settled deliberately:

- If the cohort is stored as a **predicate** and re-run downstream **as the
  originating user**, row filters stay consistent — but the downstream service
  needs that user's identity propagated.
- If a **service principal materializes** the cohort to a table, per-user
  filtering is bypassed; the cohort may contain rows the user couldn't see. That
  might be intended (a curated research extract) or a leak, depending on policy.

**Open question / future investigation:** whose identity defines a cohort's
contents at handoff time, and does the receiving service honor the same row
filters? This is at least as important as the UI design and should be resolved
before the handoff mechanism is built. (Downstream mechanics otherwise out of
scope per the author's existing plans.)

## Summary of open questions

- iframe auth inside a cross-origin embed: `SameSite`/third-party cookies, CSP
  allowances, or a token-handoff fallback.
- LLM-tool auth: service-principal impersonation vs. user JWT pass-through; how
  the user identity reaches Trino so OPA applies.
- Service-principal identity (`datasets_svc`?), token lifespan, and refresh for
  long sessions (cf. ADR 0024).
- Cohort ownership, sharing, and the identity-relative-contents question at
  downstream handoff.
- Cohort persistence backing store (app Postgres for definitions vs. a
  materialized Delta table/view for finalized cohorts) — not yet decided.
- The exact shape of the iframe ↔ Datasets-service filter-sync contract.

## Related ADRs

- **0003** — OAuth2 Proxy authentication middleware (SSO at the ingress).
- **0009 / 0012** — Open WebUI CSP and global security headers (constrain embeds).
- **0020 / 0023** — Trino OPA authorization, per-user row filters, view security
  model (why the cohort service must query as the user).
- **0022** — Trino authentication and identity propagation (service-principal
  impersonation vs. JWT pass-through patterns to reuse).
- **0024** — Token refresh for SDK Trino access (token lifespan/refresh patterns
  for long-lived sessions).
