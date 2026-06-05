# ADR 0025: In-App User Approval and Launchpad Token Pass-Through

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

## Decision

A purpose-built **approval UI in the launchpad**, backed by a small **Keycloak
REST resource**, with **dynamic, config-driven attributes**.

### 1. scout-approval REST resource (Keycloak SPI)

A `RealmResourceProvider` in the existing event-listener SPI exposes, under
`/realms/<realm>/scout-approval/`:

- `GET /schema` — the data-access attributes to collect, **discovered at request
  time** from the realm User Profile (attributes annotated `scoutAuthz=true`,
  themselves rendered from `trino_attribute_filters`). Each carries a widget hint
  (`inputType`), allowed `options`, and a safe `scoutDefault`.
- `GET /pending` — users who accepted the Terms of Use but aren't yet in
  `scout-user`.
- `POST /approve` — validate the submitted attributes against the schema, set
  them, and join `scout-user`.

Because the schema is discovered, **adding an authorization dimension stays the
one-line inventory edit ADR 0020 promised**: the new attribute flows into the
form and its server-side validation with no code change here. Safe defaults are
config-driven too (`scoutDefault`: `mask_phi_fields=true`,
`bypass_hidden_tables=false`).

Every endpoint is gated server-side on `scout-admin` group membership
(`BearerTokenAuthenticator` + a live group check). There is no standing
admin-API credential — the resource acts on the authenticated admin's own
authority.

### 2. UI in the launchpad

The approval page lives in the launchpad (Next.js + Tailwind + next-auth), not
as Keycloak-served HTML, reusing the launchpad's existing auth, styling, and
admin gating. A table of pending users opens a slide-over drawer with the
dynamic form; the admin approval email deep-links to
`…/admin/approvals?user=<id>`. The page and its Admin Tools tile gate on
`session.user.isAdmin` — the `launchpad-admin` client role that the `scout-admin`
group grants — so the UI gate matches the role Keycloak already issues. The KC
API remains the real gate (defense in depth).

### 3. Token pass-through (not impersonation, not exchange)

The browser never holds the Keycloak token. A launchpad **server-side route**
(`/api/approvals/*`) reads the admin's access token from their next-auth session
and forwards it as the Bearer to the scout-approval API — **same-realm JWT
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
stays reusable for the SSO session.

**Token storage note.** next-auth's default JWT-session strategy keeps the
access + refresh tokens in the session cookie, JWE-encrypted with
`NEXTAUTH_SECRET`, `httpOnly`, `secure` — not readable by browser JS. This is
pre-existing launchpad behavior, not introduced here. For a PHI system the more
conservative pattern is a **server-side session store** (no token material in
the browser at all); that is a launchpad-wide auth change, tracked as a future
hardening and independent of this feature.

## Consequences

- Approving a user is one validated screen instead of several hand-edits in the
  Keycloak console.
- The first `scout-admin` is still seeded out-of-band by the Keycloak master
  admin — the privileged role is granted via the console, not self-service; this
  UI only manages `scout-user`.
- The launchpad becomes a (Trino-less) consumer of a Keycloak REST API using the
  user's own token — a new identity-propagation path, but consistent with the
  pass-through model in ADR 0022.
- The initial proof-of-concept (a Keycloak-SPI-served HTML page + a public PKCE
  client) is retired in favor of the launchpad page.

## References

- ADR 0020 (OPA attribute model), [ADR 0022](0022-trino-auth-and-impersonation.md)
  (Trino auth + identity propagation), [ADR 0024](0024-sdk-trino-token-refresh.md)
  (SDK token refresh)
- next-auth session strategies; RFC 9700 (OAuth 2.0 Security Best Current Practice)
