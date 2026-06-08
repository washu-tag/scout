# External API Access to Scout — Design Exploration

> **Status:** Exploratory. No decision has been made. This document captures a design
> discussion about whether and how to give clients *outside* the cluster programmatic
> (non-browser) access to Scout services. It is **not** an ADR — nothing here is committed.
> If/when a direction is chosen, that decision should be recorded as an ADR.

Related: [ADR-0003 (OAuth2 Proxy)](adr/0003-oauth2-proxy-authentication-middleware.md),
[ADR-0022 (Trino auth & identity propagation)](adr/0022-trino-auth-and-impersonation.md),
[ADR-0024 (SDK Trino token refresh)](adr/0024-sdk-trino-token-refresh.md),
[authentication.md](authentication.md), [xnat-and-plugin-deployment.md](xnat-and-plugin-deployment.md).

## Motivation

The motivating use case is **XNAT REST API access from a CLI script running outside
Scout** — a workflow extremely common among XNAT users today, which we'd like to support
for XNAT-in-Scout. Secondary, personally-motivated cases came up too: starting Temporal
workflows via an API instead of `kubectl exec`, automating "enable my user / promote to
admin" on a dev box, etc.

"API gateway" was the initial framing, but the discussion reframed it: Scout doesn't lack
a gateway (Traefik is one). It lacks an **API *auth path*** — a way for a browserless
client to authenticate at the edge.

## Key reframings from the discussion

These corrected the initial framing and shaped everything below:

1. **Scout's edge is browser-only today.** Every external door is oauth2-proxy as a Traefik
   `forwardAuth` middleware. On a missing/invalid session it returns a **302 redirect** to
   the Keycloak login page (see the error middleware in
   `ansible/roles/oauth2-proxy/tasks/deploy.yaml`). A CLI/script gets an HTML login page,
   not a `401`. **This is the actual blocker** — not any missing token mechanism.

2. **Trino's JWT auth is *internal*, not an external API path.** Trino validates Keycloak
   JWTs directly (`ansible/roles/trino/templates/values.yaml.j2`,
   `http-server.authentication.type=PASSWORD,JWT`), but it is NetworkPolicy-restricted to
   in-cluster callers and never exposed through Traefik. It proves Scout *backends can
   validate Keycloak JWTs and propagate identity* — it does not provide an external entry
   point. All three ADR-0022 identity-propagation patterns (Jupyter JWT pass-through;
   Superset/Voila JWT + `X-Trino-User` impersonation; Open WebUI MCP HTTP-Basic +
   impersonation) are internal-caller patterns.

3. **XNAT-in-Scout is an internal service.** We deploy XNAT *inside* Scout; XNAT is unaware
   Scout exists; Scout→XNAT calls are internal (cluster DNS). The API-edge question is the
   *reverse* direction: external clients reaching XNAT's API.

4. **The "browser vs. API" two-path model.** The clean mental model is two auth chains on
   the same Traefik:

   | | Browser path (today) | API path (new) |
   |---|---|---|
   | Auth | oauth2-proxy session cookie | Keycloak JWT in `Authorization` header |
   | Failure | `302` → Keycloak login | `401` + `WWW-Authenticate: Bearer` |
   | Token source | auth-code flow in browser | offline token / CLI |
   | Routing | Ingress + `oauth2-proxy-*` middleware | Ingress + JWT-validating middleware |

   oauth2-proxy can serve both at once via `skip_jwt_bearer_tokens` (a valid bearer JWT
   passes through without the redirect; browser users still get the redirect). Worth
   confirming during implementation: that oauth2-proxy still enforces the `allowed_roles`
   approval gate (`oauth2-proxy:oauth2-proxy-user`) on passthrough bearer tokens — if so,
   ADR-0003's approval gate is preserved for API access too.

## Chosen direction (tentative)

Unify on **Keycloak-issued bearer tokens** for external API access, rather than leaning on
each service's native token system (e.g. XNAT alias tokens). Rationale: one identity, one
locally-stored credential, consistent with how Scout already treats `preferred_username` as
the canonical principal everywhere internal (Trino `principal-field`, oauth2-proxy
`X-Auth-Request-Preferred-Username`, `X-Trino-User`).

Two refinements landed during discussion:

- **The stored credential should be an *offline token*, not a static long-lived access
  JWT.** Scout access tokens are deliberately short (ADR-0024 ties
  `keycloak_access_token_lifespan` to both the realm and the Hub refresh gate). A long-lived
  access JWT in a file is **unrevocable until expiry**; an offline refresh token is
  long-lived *and* revocable (kill the session in Keycloak), and the CLI/SDK mints
  short-lived access JWTs from it — reusing the ADR-0024 refresh machinery. To the user it
  feels like `gh auth login`: authenticate once, calls "just work."
- **Use a dedicated CLI/API Keycloak client** so its lifespans/scoping/revocation are
  independent of the browser clients and don't perturb the ADR-0024 Hub gate.

### Audience: do NOT use a single universal `scout_api` audience

An early idea — one audience every service accepts — was rejected. **You can only assert an
audience on a validator you control:**

| Service | Who controls bearer validation | Can honor a Scout audience? |
|---|---|---|
| XNAT | us (a plugin we'd write) | yes |
| Trino | us (our deploy) | already requires `aud=trino` |
| oauth2-proxy edge | us | yes |
| a BFF, if built | us | yes |
| Superset / Grafana / Temporal / MinIO APIs | the upstream projects | **no** — their OIDC clients are browser-login only; they don't do Keycloak-bearer API auth |

So external bearer-token API access realistically only ever applies to **XNAT, Trino, and
anything we build**. That surface is small enough that no shared audience is needed: mint
tokens narrowly targeted at the intended resource (`aud` includes `trino` for Trino — its
`required-audience` is a membership check, so a multi-valued `aud` works; XNAT's plugin
validates whatever we tell it). Most other services are simply out of scope for
bearer-token API access regardless of what we do.

## What would be required to set up an API edge

Roughly in dependency order:

1. **A bearer-token auth path at the edge.** Either enable `skip_jwt_bearer_tokens` on the
   existing oauth2-proxy, or add a parallel Traefik `forwardAuth`/middleware variant whose
   failure response is `401` (not the `sign_in` 302). Routing is unchanged from today —
   per-service Kubernetes Ingress with the appropriate middleware annotations.

2. **A token-acquisition story for clients.** A dedicated Keycloak client + an offline-token
   flow, plus a small CLI/SDK helper (`scout login` / token mint + transparent refresh,
   reusing ADR-0024 logic).

3. **Per-service token acceptance**, only for services we control:
   - **XNAT**: a bearer-trust plugin (validate Keycloak JWT against JWKS, map to an XNAT
     user). A POC of exactly this existed earlier (`scout-xnat-auth-plugin`, bearer-only
     token-trust) but was set aside in favor of the openid plugin for browser SSO; it was a
     JDK 8 build and would need rebuilding for JDK 21 / XNAT 1.10.0.
   - **Trino** (if exposed externally at all): it already validates JWTs; the work is
     exposing it through a bearer edge rather than keeping it internal-only.

4. **Identity consistency** (see open questions): the bearer plugin and the openid plugin
   must resolve the *same human* to the *same XNAT account*.

## Easy / Hard / Open

### Easy (config or small, self-contained work)

- **Bearer passthrough at the edge** — an oauth2-proxy flag (`skip_jwt_bearer_tokens`) or a
  small Traefik middleware variant.
- **Routing a new/exposed API** — identical to the existing per-service Ingress +
  middleware-annotation pattern Scout already uses everywhere (subdomain-based).
- **Token *issuance* with GitHub-PAT-like UX** — Keycloak natively provides:
  offline tokens (long-lived, individually revocable), per-token expiry, custom **optional
  client scopes** that land in the `scope` claim only when requested
  (`scope=openid xnat:read`), scope→audience mappers, and token exchange (RFC 8693; verify
  Keycloak version — was preview for a while) for downscoping. This is realm config plus a
  thin CLI helper. The GitHub-token *experience* (named, scoped, expiring, revocable tokens)
  is cheaply achievable.
- **Aligning XNAT's username to `preferred_username`** — a one-line config change, **not** a
  code change. The openid plugin's `usernamePattern` resolves any claim present in the
  id_token/userinfo (`OpenIdConnectUserDetails.resolvePattern()` falls back to the raw claims
  map), so `usernamePattern=[preferred_username]` works as-is. (The default
  `[providerId]-[sub]` is the *safe* choice — `sub` is stable/unique/namespaced — but it
  makes XNAT the lone service not keyed on `preferred_username`.)

### Hard (real custom work, per service)

- **An XNAT bearer-trust plugin** — Java plugin: validate JWT, and (if scoped) map
  `scope`/claims onto XNAT operations. XNAT has no native "read-only session" concept, so
  scope enforcement (e.g. reject mutating verbs without `xnat:write`) is filter-level custom
  code bounded by XNAT's own permission model.
- **Scope *enforcement* in Trino** — Trino authz is identity/attribute-based via OPA
  (ADR-0020), not token-scope-based. Honoring scopes means feeding the `scope` claim into
  OPA input and writing policy — doable but against the grain.
- **Uniform, cross-service GitHub-style scope enforcement** — *not possible with what we
  have.* GitHub gets this "for free" because it owns 100% of its resource servers and built
  scope-checking into all of them. Scout is a federation of third-party services with
  heterogeneous auth and no shared chokepoint. A scope means nothing until something we own
  inspects it, so the only way to get uniform enforcement across services we don't control
  is to introduce a central enforcement point — i.e. a gateway/BFF. **If uniform scope
  enforcement ever becomes a hard requirement, that is the strongest justification for
  building the gateway** (a much better reason than the routing convenience originally
  floated).

### Open questions

1. **Is there a concrete external consumer beyond XNAT-CLI?** Both candidates I'd have
   reached for (XNAT, Trino) turned out to be internal services; the broader "API for
   everything" lacks named consumers. The personally-motivated cases (Temporal workflow
   start, self-service admin) are real but low-value and heterogeneous. Without named
   consumers, anything beyond the XNAT slice is premature.

2. **XNAT identity migration.** Switching `usernamePattern` to `[preferred_username]`
   orphans already-provisioned `keycloak-<sub>` accounts (the mapping in
   `xhbm_xdat_user_auth` is keyed on the resolved pattern; a new login → new empty account).
   Options: (a) **dev: recreate** — easiest, and the reason to flip this *now*, before
   there's data to lose; (b) `UPDATE xhbm_xdat_user_auth SET auth_user=<preferred_username>`
   for continuity without renaming the XNAT login; (c) a full XNAT login rename (messy —
   logins are FK'd across projects/permissions). Also confirm what Keycloak emits as
   `preferred_username` for federated (GitHub/institutional) users and whether it's
   unique/stable enough — Scout has *already* bet on this via Trino's `principal-field`, so
   aligning XNAT is consistent with an existing platform decision, but the uniqueness
   assumption should be verified.

3. **Does oauth2-proxy enforce `allowed_roles` on passthrough bearer tokens?** If yes, the
   ADR-0003 approval gate extends to API access automatically (token must carry the role
   claim). If no, the gate needs to be re-implemented on the API path. Must be verified.

4. **Offline-token vs. genuinely-long-lived-token.** Offline token (revocable, needs a
   refresh helper) is recommended; a static long-lived JWT (no refresh code, but
   unrevocable) is a deliberate alternative with a security tradeoff. Not yet decided.

5. **Scope taxonomy.** If we adopt scopes, what's the initial set (`xnat:read`,
   `xnat:write`, `trino:query`, …) and which are enforced vs. issued-but-not-yet-enforced?

## The API monolith / BFF idea (considered, shelved)

A "thin client that accepts the bearer token then proxies to backends using its own static
service credentials + the impersonated user" was discussed. Verdict: **not a bad idea as a
complement, but not a universal substitute, and specifically the wrong tool for XNAT.**

- **Good fit:** Trino (clean `X-Trino-User` impersonation primitive already exists; avoids
  exposing Trino externally) and *curated narrow operations* over powerful internal services
  (start a Temporal workflow; enable/promote a user) — you don't want to expose Temporal's
  full gRPC frontend or Keycloak's whole admin API.
- **Poor fit: XNAT.** No impersonation primitive (so it doesn't spare you the plugin),
  rich native API that clients expect to hit directly, and bulk DICOM transfers you don't
  want to double-proxy. XNAT wants direct routing + the bearer plugin.
- **Security caveat:** a service holding static creds to every backend and able to
  impersonate any user is a maximal confused deputy — whole-platform blast radius. Keep it
  least-privilege and audit-logged if built.

Shelved for now per the discussion; revisit only with named consumers or a hard uniform-scope
requirement.

## Tentative recommendation

1. Solve the **XNAT-CLI** case first as a self-contained slice: bearer passthrough at the
   edge + an XNAT bearer-trust plugin + offline-token CLI flow. Drop the universal audience;
   mint per-target tokens.
2. Align XNAT to `preferred_username` **now**, while dev users are disposable.
3. Adopt Keycloak scopes on the **issuance** side early (cheap, good UX); enforce them only
   where we control the validator and it's worth it (start with XNAT read/write).
4. Treat the broader gateway/BFF as deferred — build it only if named consumers appear or
   uniform cross-service scope enforcement becomes a hard requirement.
5. When a direction is committed, record it as an ADR (this generalizes ADR-0022's internal
   identity model to external clients).
