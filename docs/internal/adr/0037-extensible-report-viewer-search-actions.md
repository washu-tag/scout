# ADR 0037: Extensible Report-Viewer Search Actions

**Date:** 2026-09
**Status:** Accepted
**Decision Owner:** TAG Team

## Context

report-viewer's search-detail toolbar (ADR 0029) ships two buttons, Explain Search and
Download CSV, both hardcoded in the React frontend. Adding, removing, or gating a third
button needs a source change, an image rebuild, and a redeploy — the same problem ADR
0034 solved for the launchpad's front door, showing up one level in on a specific page.
Issue #595 ("Pluggable Apps") separately describes a tier of genuinely separate,
independently-deployed "Apps" that Scout should be able to hand off to without knowing
what they do. This work is a proof of concept for both: a button becomes data, and one
kind of button can call out to a real external service.

The harder question was gating: some buttons (an admin-only tool, a not-yet-GA
integration) shouldn't be visible to every researcher. The first design mirrored ADR
0034's own answer for launchpad chips — Keycloak client roles, read from a Bearer JWT's
`resource_access` claim — and went as far as provisioning a real `report-viewer` Keycloak
client, a `report-viewer-admin` role, and a role-mapper scoped to it. Live testing then
surfaced a fact the design had gotten wrong: report-viewer has two inbound auth paths
(ADR 0029) — a Bearer JWT, used only by OWUI's server-side tool calls, and an
oauth2-proxy-forwarded header, used by every request the SPA's own frontend makes.
Reading `frontend/src/api/client.ts`'s `api()` function confirmed it never attaches an
`Authorization` header — every fetch the browser makes takes the header path, which
carried no role or group claim at all. A role-gated action could therefore never appear
in the real product UI, for anyone, no matter how correctly Keycloak was configured: the
JWT path it depended on isn't reachable from anything that renders a toolbar.

## Decision

Two decisions, layered on top of each other.

### Actions are declared data, rendered generically

`ActionDescriptor` (`report-viewer/src/scout_report_viewer/actions.py`): `id`, `title`,
`weight`, `action_type`, `url`, `required_group`, `client_handler`, `endpoint_url`,
`invoke_token` (`Field(exclude=True)`, never round-trips to the browser). The chart
renders today's two real buttons plus any site-admin-authored `actions.custom` entries
into a ConfigMap mounted directly into the pod at `settings.action_catalog_path`, read
once at process start — the same "core chips ride a chart-rendered ConfigMap" delivery
ADR 0034 uses for the launchpad's *own* tiles, deliberately not attempting that ADR's
cross-namespace sidecar-discovery increment here (every action today ships from
report-viewer's own chart; nothing yet needs a third party to contribute one). Graded
degradation mirrors ADR 0034: an unparseable file falls back to `_DEFAULT_CATALOG`; one
invalid entry is skipped and logged without affecting the rest; a duplicate `id` rejects
the later entry; a genuinely empty file yields a genuinely empty catalog rather than
silently refilling the built-in defaults.

Three `action_type` values, matched to what the SPA can do with a click:

- **`open-url`** — the SPA opens `url` via a real anchor-click navigation, not
  `window.open()`, with an always-attempted copy-link fallback
  (`frontend/src/openResult.ts`). This matters once a link is opened from OWUI's
  sandboxed chat iframe: a destination must serve `COOP: unsafe-none` to be openable as
  a popup from there at all (the binary check is in the WHATWG HTML navigation
  algorithm) — the `security-headers-sameorigin-popups` Traefik middleware
  (`ansible/roles/traefik/tasks/main.yaml`) exists because of this.
- **`client`** — the SPA looks up `client_handler` in a small local registry of
  page-specific logic a backend payload can't describe (e.g. building a CSV from the
  currently loaded/filtered rows). Structurally not authorable via `actions.custom` — a
  handler has to exist in the frontend first.
- **`backend-call`** — the SPA POSTs to
  `/api/searches/{id}/actions/{action_id}/invoke`; report-viewer forwards the search's
  context to `endpoint_url` (a genuinely separate, independently-deployed service — the
  "Apps" tier from #595) and relays back whatever `{"url": ...}` it returns, then hands
  that off to the same `open-url` handling. report-viewer never inspects what the target
  service actually does — only that it returns a safe `http(s)` URL. `invoke_token`, if
  set, is forwarded as `X-Report-Viewer-Action-Token`. `xnat-explore-poc`
  (`xnat-explore-poc/`, `helm/xnat-explore-poc/`) is the reference implementation: a
  deliberately fake FastAPI service with its own Helm chart and a NetworkPolicy
  restricting ingress to report-viewer's namespace, deployed independently of any
  Ansible role — proving the mechanism crosses a real service boundary without doing any
  real XNAT integration work.

### Visibility gates on Keycloak group membership, not client roles

`list_actions(user_groups)` filters the catalog server-side. Visibility is UX, not the
authorization boundary (ADR 0034's framing, unchanged here) — a real `backend-call`
action must still enforce its own check independently, since a hidden action's URL is
not itself a secret.

Group membership, not a client role, because it's the only claim that actually reaches
the SPA's own requests. A genuine Keycloak Group (`scout-admin` already exists and
already grants admin capability on several other Scout services) is surfaced through a
new `oidc-group-membership-mapper` (`groups-mapper`) on **oauth2-proxy's own client** —
`claim.name=groups`, `full.path=false` so the claim carries bare names (`scout-admin`),
not paths (`/scout-admin`). oauth2-proxy is configured with `oidc_groups_claim = "groups"`
(spelled out explicitly even though it matches oauth2-proxy's own default, so the
coupling to the mapper's claim name is visible) and already ran with
`set_xauthrequest = true`, so `/oauth2/auth`'s response now carries
`X-Auth-Request-Groups`. Traefik's `oauth2-proxy-auth` forwardAuth middleware
(`ansible/roles/oauth2-proxy/tasks/deploy.yaml`) is widened to copy that header onto the
upstream request alongside the existing `X-Auth-Request-Preferred-Username`.

**Trust model.** Traefik's `authResponseHeaders` always overwrites any client-supplied
header of the same name with the value from oauth2-proxy's own server-computed
`/oauth2/auth` response — a request that sets `X-Auth-Request-Groups: scout-admin`
directly gets that header replaced before report-viewer ever sees it, unless it actually
goes through Traefik's forwardAuth and authenticates as a `scout-admin` member. The
existing `X-Report-Viewer-Gateway` shared secret is the second half: it stops a pod that
bypasses Traefik/oauth2-proxy entirely (hitting report-viewer's Service directly) from
forging either header, since only Traefik's middleware injects that secret.
`auth.py`'s `User.groups` is populated only on this oauth2-proxy header path; the Bearer
JWT path (OWUI's server-side tool calls) always yields an empty set, so a group-gated
action is structurally unreachable from that surface too — a deliberate scope narrowing,
since that path's callers never render the toolbar in the first place.

The `report-viewer` Keycloak client, its `report-viewer-admin` role, and the
client-role protocol mapper — all provisioned earlier in this same effort, before the
architecture problem surfaced — are removed as dead scaffolding: `resource_access`-based
checking never had a reachable code path.

## Consequences

- Adding, removing, or gating an `open-url`/`backend-call` button is a `values.yaml`
  change (`actions.custom`) plus `helm upgrade` — no report-viewer code change or image
  rebuild. `client` actions still need a source change; there is structurally no values
  field for one.
- `requiredGroup: <keycloak-group>` is the entire gating surface. The reverse — finer
  gating than group membership — has no path today; a future need has to invent a new
  mechanism, not extend this one.
- report-viewer gains one new inbound header (`X-Auth-Request-Groups`) and Keycloak
  gains one new client protocol mapper; both are wholly owned by this feature and can be
  removed without touching any of the SPA's other functionality.
- Config drift across a rename is possible and silent: during development, an
  inventory's custom-action entry kept the old `requiredRole` key after the chart moved
  to `requiredGroup`, and Helm's silent-ignore of unrecognized map keys turned that into
  an *ungated* button rather than a render error. Not solved here; a candidate follow-up
  is schema validation on `actions.custom` (`required` already guards the mandatory
  fields, but not renamed/retired ones).
- The catalog is read once at process start; a ConfigMap edit needs the pod to restart
  to take effect. The chart's `checksum/actions-configmap` Deployment annotation already
  forces this on every relevant change, but there is no live re-read or TTL snapshot,
  unlike ADR 0034's launchpad catalog.
- Cross-namespace sidecar discovery (ADR 0034's mechanism for third-party contributions)
  is not attempted here. A future need for actions contributed by a chart other than
  report-viewer's own would require that increment.

## Alternatives Considered

| Option | Verdict |
| --- | --- |
| Keycloak client roles via `resource_access` (Bearer JWT), gating in the JWT-validation path | Rejected: unreachable from the SPA's own requests (`client.ts` never sends a Bearer token) — a role-gated action could never appear in the real UI for anyone |
| A report-viewer-owned OIDC scope forcing role claims onto the oauth2-proxy session | Rejected: reinvents Keycloak's existing group-membership mapper with more moving parts, for the same header-trust guarantee the group approach gets for free |
| Cross-namespace sidecar ConfigMap discovery (full ADR 0034 parity) | Deferred: every action today ships from report-viewer's own chart; no third-party-contribution use case yet to justify the RBAC/sidecar cost |
| Client-side-only visibility (hide with CSS/JS, no server-side filter) | Rejected outright: the descriptor — including any `invoke_token` — would round-trip to every browser regardless of group membership |
