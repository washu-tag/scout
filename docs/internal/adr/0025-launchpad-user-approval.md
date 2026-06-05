# ADR 0025: In-App User Administration and Launchpad Token Pass-Through

**Date**: 2026-06-05
**Status**: Accepted
**Decision Owner**: TAG Team

## Context

ADR 0020 makes per-user Trino access attribute-driven: a Scout user's row
filters and column masks come from Keycloak User Profile attributes
(`allowed_facilities`, `mask_phi_fields`, `bypass_hidden_tables`, ...), rendered
from `trino_attribute_filters`. Granting a new user access therefore means two
manual Keycloak master-admin-console steps — add them to `scout-user`, then
hand-edit each attribute on their user detail page. We have no upstream IdP data
to automate the decision, so a human stays in the loop; but the console workflow
is error-prone (free-text attributes, no validation, easy to miss a step) and
needs master-admin access. We want to make the approver's job easier without
weakening the authorization model.

This started as an approval-only UI and has since grown into the primary surface
for **user administration**: beyond approving pending users, an admin can edit an
existing user's data-access attributes, promote/demote `scout-admin`, and offboard
(revoke all Scout access). Keycloak remains available for power users, but the
launchpad is the main touch-point, so the standalone Keycloak "Users" tile is
removed from the launchpad home.

## Decision

A purpose-built **user-administration console in the launchpad**, backed by a
small **Keycloak REST resource**, with **dynamic, config-driven attributes**.

### 1. scout-users REST resource (Keycloak SPI)

A `RealmResourceProvider` in the existing event-listener SPI exposes, under
`/realms/<realm>/scout-users/`, a `scout-admin`-gated API (`BearerTokenAuthenticator`
+ a live `scout-admin` group check on every call; no standing admin-API
credential — the resource acts on the authenticated admin's own authority):

- `GET /schema` — the data-access attributes to collect, **discovered at request
  time** from the realm User Profile (attributes annotated `scoutAuthz=true`,
  themselves rendered from `trino_attribute_filters`). Each carries a widget hint
  (`inputType`), allowed `options`, and a safe `scoutDefault`.
- `GET /pending` — users who accepted the Terms of Use but aren't yet in
  `scout-user`.
- `GET /users?status=&search=` — the admin table: each user's derived status
  (pending / active / admin), admin flag, and **only** their `scoutAuthz`
  attributes — never the raw attribute map, so `scout_terms_accepted_at` and the
  approval-email marker don't leak to the client.
- `POST /approve` — validate the submitted attributes against the schema, set
  them, and join `scout-user`.
- `POST /users/{id}/attributes` — edit an approved user's data-access attributes
  (reuses the approve validation; no group change).
- `POST` / `DELETE /users/{id}/admin` — promote / demote `scout-admin`.
- `DELETE /users/{id}/membership` — offboard: remove `scout-user` **and**
  `scout-admin` (full revocation; the user falls back to Pending).

Because the schema is discovered, **adding an authorization dimension stays the
one-line inventory edit ADR 0020 promised**: the new attribute flows into the
form and its server-side validation with no code change here. Safe defaults are
config-driven too (`scoutDefault`: `mask_phi_fields=true`,
`bypass_hidden_tables=false`).

**Guardrails** (package-private predicates, mutation-tested, enforced server-side
regardless of the UI):

- *Last-admin lockout* — demoting or offboarding the last `scout-admin` is
  rejected with 409 (members counted via an indexed `getGroupMembersStream`
  capped at 2, not a full scan). A TOCTOU race on two concurrent removals is
  accepted for these small realms; recovery is the Keycloak bootstrap below.
- *No self-offboard* — an admin can't revoke their own access (almost always an
  accident). Self-*demote* is allowed (a privilege drop), gated only by last-admin.
- *Attribute-key allowlist* — only `scoutAuthz` keys may be written, and the whole
  submission is validated before anything is set, so a bad request never
  half-applies.

The launchpad proxy and the UI's confirm dialogs are convenience only; a `curl`
with a valid `scout-admin` token skips them, so only these server-side checks are
load-bearing.

### 1a. Propagation: every mutation emits a Keycloak admin event

The SPI mutates the user model directly (`joinGroup` / `setAttribute`), which —
unlike a change made through Keycloak's admin REST API — fires **no admin event**.
But both consumers of user changes react *only* to admin events: the OPA bundle
publisher (ADR 0021) re-snapshots a user on a `USER` / `GROUP_MEMBERSHIP` event,
and the approval/offboard email listener keys off `scout-user` in a
group-membership event. So every mutating endpoint explicitly emits a
correctly-shaped `AdminEvent` (`resourcePath users/{id}/...`) after the model
change, built from the calling admin's `AuthResult`.

Without this an approved user would sit in `scout-user` in Keycloak yet be denied
every row by OPA until an unrelated admin event or a Keycloak restart re-seeded
the bundle — a latent defect in the approval-only proof-of-concept, fixed here.
The tested core methods stay event-free; emission lives in the thin JAX-RS
adapters.

### 1b. Console vs Keycloak boundary

The launchpad console owns the `scout-user` / `scout-admin` lifecycle and the
data-access attributes. Keycloak stays the escape hatch for everything else:
account **enable/disable** (deliberately out of scope — `enabled` is not a
`scoutAuthz` attribute, so it never appears in the form), **first-admin
bootstrap**, and realm / client / IdP / SMTP configuration. A fresh realm is
**console-locked** until the first `scout-admin` is granted in the Keycloak master
console — every console endpoint is `scout-admin`-gated, and the last-admin guard
makes this stricter, not looser. The in-console "Open in Keycloak" link and the
Keycloak admin-events log (the recommended audit trail) reach Keycloak for power
users.

**Blast radius (accepted).** `scout-admin` maps to `realm-management/realm-admin`,
so in-UI promotion hands over full Keycloak realm-admin, not just Scout-app admin.
This is accepted for the current trust model — a single admin can promote another,
no dual-control. Narrowing what `scout-admin` grants, or requiring a second
approver on promote, is left as future work.

### 2. UI in the launchpad

The console lives in the launchpad (Next.js + Tailwind + next-auth) at
`/admin/users`, not as Keycloak-served HTML, reusing the launchpad's existing
auth, styling, and admin gating. One users table with a Pending | Active | Admins
filter; clicking a row opens a slide-over drawer that is a **dual-mode editor** —
approve a pending user (form seeded from schema defaults) or manage an existing
one (pre-filled with their current attributes), with promote/demote and offboard
behind confirm prompts that surface the server's 409 rather than assuming success.
The admin approval email deep-links to `…/admin/users?user=<id>`. The page and its
Admin Tools tile gate on `session.user.isAdmin` — the `launchpad-admin` client
role that the `scout-admin` group grants — so the UI gate matches the role
Keycloak already issues. The KC API remains the real gate (defense in depth).

### 3. Token pass-through (not impersonation, not exchange)

The browser never holds the Keycloak token. A launchpad **server-side route**
(`/api/users/*`) reads the admin's access token from their next-auth session
and forwards it as the Bearer to the scout-users API — **same-realm JWT
pass-through**, the model [ADR 0022](0022-trino-auth-and-impersonation.md) uses
for JupyterHub. This fits the launchpad because, like Jupyter's Hub, it has a
**fresh-user-token source**: next-auth is the custodian, holding the refresh
token and renewing server-side. (The impersonation clients — Superset, Voila,
the Open WebUI MCP — use `X-Trino-User` precisely because they lack such a
source; the launchpad does not need to.) There is **no token exchange** (cf. the
closed XNAT POC, #410).

Keeping the bearer valid follows [ADR 0024](0024-sdk-trino-token-refresh.md)'s
proactive + reactive shape: the proxy refreshes via the stored refresh token
when the cached access token is within 15s of expiry, and retries once on a KC
401. Refresh-token rotation is off in the realm, so the stored refresh token
stays reusable for the SSO session. The route is a catch-all (`[...path]`) over
`GET`/`POST`/`DELETE` with a per-method allowlist of path shapes, so it forwards
the sub-resourced verbs without becoming an open proxy.

**Token storage note.** The session cookie holds **only the refresh token** (plus
an `isAdmin` flag), JWE-encrypted with `NEXTAUTH_SECRET`, `httpOnly`, `secure`; the
`/api/users` proxy mints a fresh access token from it per request. We deliberately
do **not** cache the access token in the cookie: an admin's token carries dozens of
roles (the launchpad client is `fullScopeAllowed`), and storing it pushed the
(chunked) cookie past the browser/proxy size limit, intermittently dropping the
session — a real bug this feature hit in testing. Keeping only the refresh token
bounds the cookie to a single small chunk. Two complementary hardenings remain:
trimming the launchpad client's role scope (`fullScopeAllowed=false`, so its token
stops carrying realm-admin), and the more conservative **server-side session
store** (no token material in the browser at all).

## Consequences

- User administration is one validated console — approve, edit attributes,
  promote/demote, offboard — instead of several hand-edits in the Keycloak master
  console, and without master-admin access.
- The first `scout-admin` is still seeded out-of-band in the Keycloak master
  console (the realm is console-locked until then); the console manages the
  `scout-user` / `scout-admin` lifecycle from there on.
- Every SPI mutation now emits an admin event, so OPA propagation and the
  approval/offboard emails work for SPI-driven changes — they silently did not
  before (§1a).
- Offboard is full revocation (both groups), so there is never an "offboarded but
  still realm-admin" state; an offboarded user reappears under Pending (Terms
  still accepted), not deleted.
- The launchpad becomes a (Trino-less) consumer of a Keycloak REST API using the
  user's own token — a new identity-propagation path, but consistent with the
  pass-through model in ADR 0022.
- The UI admin gate (`session.user.isAdmin`) is login-time, so a user
  demoted/offboarded via the console keeps the admin affordances until their
  session token refreshes; the SPI's live `scout-admin` check still rejects every
  call in the meantime, so this is a stale affordance, not an access path.
- The audit trail is the Keycloak admin-events log (reached via "Open in
  Keycloak"); shipping actions to an external immutable store is future work.
- The initial proof-of-concept (a Keycloak-SPI-served HTML page + a public PKCE
  client) is retired in favor of the launchpad page.

## References

- ADR 0020 (OPA attribute model), [ADR 0021](0021-opa-user-attribute-distribution.md)
  (OPA user-attribute distribution via admin events),
  [ADR 0022](0022-trino-auth-and-impersonation.md) (Trino auth + identity
  propagation), [ADR 0024](0024-sdk-trino-token-refresh.md) (SDK token refresh)
- next-auth session strategies; RFC 9700 (OAuth 2.0 Security Best Current Practice)
