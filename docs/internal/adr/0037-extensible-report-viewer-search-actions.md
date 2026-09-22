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
what they do. This work makes both real: a button becomes data, and one kind of button
can call out to a genuinely separate service — though `xnat-explore-poc`, the example
used to build and test that hand-off, remains a deliberately fake demo (see below), not
itself a production Pluggable App.

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
`weight`, `action_type`, `url`, `required_group`, `client_handler`, `endpoint_url`. A
`backend-call` entry's invoke token is deliberately not a field on this model at all —
see below. The chart
renders today's two built-in buttons — each independently toggleable and group-gateable
via its own `actions.explainSearch`/`actions.downloadCsv` object (`enabled`,
`requiredGroup`) — plus any site-admin-authored `actions.custom` entries, into a
ConfigMap mounted directly into the pod at `settings.action_catalog_path`, read
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
  `/api/searches/{id}/actions/{action_id}/invoke`; report-viewer resolves the search's
  saved `sql` to concrete `{primary_report_identifier, accession_number}` pairs (one row
  per report, same `max_cohort_rows` cap as `GET /rows`; `accession_number` rides along
  as a nullable, non-unique correlation field, not an identifier — it can repeat across
  reports or be absent) and forwards those alongside the raw `sql` to `endpoint_url` (a
  genuinely separate, independently-deployed service — the "Apps" tier from #595) —
  `sql` stays in the payload for a target that genuinely needs the query itself, not
  just the resolved cohort. It relays back whatever `{"url": ...}` the target returns,
  then hands that off to the same `open-url` handling. report-viewer never inspects what
  the target service actually does with the cohort — only that it returns a safe
  `http(s)` URL. Resolving the cohort server-side means the target never needs Trino
  access or its own OPA-authorized query path (ADR 0020) to find out what it was invoked
  for, even though it's also handed the raw `sql`. If an action has an invoke token,
  it's forwarded as `X-Report-Viewer-Action-Token` — read from a Secret-backed volume
  keyed by the action's `id` (`actions-secret.yaml`, `actions.load_invoke_token()`), not
  from the action catalog itself: the catalog is a ConfigMap, which has no
  access-control distinction from other application config, so secret material never
  belongs in it. `xnat-explore-poc`
  (`xnat-explore-poc/`, `helm/xnat-explore-poc/`) is the reference implementation: a
  deliberately fake FastAPI service with its own Helm chart and a NetworkPolicy
  restricting ingress to report-viewer's own pods specifically (namespace plus pod
  selector — a bare namespace match would admit any pod sharing that namespace, not
  just report-viewer), deployed independently of any
  Ansible role — proving the mechanism crosses a real service boundary without doing any
  real XNAT integration work. Its own copy of the shared invoke token is likewise a real
  Secret, not a plain Deployment env value.

### The invoke boundary independently verifies the caller, not just a shared secret

`X-Report-Viewer-Action-Token` proves only that the caller knows a shared secret — it
says nothing about which end user the call is for or what they're authorized to do.
Without more, a target that trusts it alone is fully dependent on report-viewer's own
`requiredGroup` check never having a bug, and anything that obtains the token (a log
line, a captured trace, another compromised in-cluster workload) can invoke the action
as any user with any cohort. This directly contradicts the framing above — "visibility
is UX, not the authorization boundary... a real action must still independently enforce
the same group check at its own endpoint" — unless the target actually has something to
check.

So `invoke_search_action` also mints `X-Report-Viewer-User-Assertion`: a short-lived
(60s) HS256 JWT carrying `sub`, `groups`, `search_id`, and `action_id` — everything a
target needs to make its own authorization decision, plus enough to reject a captured
assertion replayed against a different search once its window closes.

The signing key is a **second, separate** per-action secret
(`<action_id>.assertion-key` in the same Secret-backed volume as the invoke token, set
via `actions.custom[].assertionKey`) — never the invoke token itself. Reusing the invoke
token as the signing key would mean anyone who obtained it (which travels on every
`/invoke` call and can leak via logs or traces) could also forge arbitrary user/group
claims, defeating the entire point of a signature the caller couldn't otherwise produce.
The two secrets have different exposure profiles: the invoke token is transmitted on the
wire on every call, while the assertion key never is — only its signature output goes
out, which can't be reversed to recover the key. This protects against the invoke token
leaking via an ordinary operational mistake; it does not protect against report-viewer's
own pod or the Secret object itself being compromised, which exposes everything
regardless of how many distinct values exist.

`xnat-explore-poc`, as the reference implementation, actually verifies this rather than
trusting the shared secret alone: signature and expiry via `assertionKey`, `search_id`
bound to the current request, and an optional `requiredGroup` membership check against
the asserted `groups` — demonstrating what a real App is expected to do, not just what
report-viewer sends.

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
- `explainSearch`/`downloadCsv` are objects (`enabled`, `requiredGroup`), not plain
  booleans, deliberately kept out of `actions.custom` — Helm deep-merges map values but
  replaces list values wholesale, so a site overriding `actions.custom` to add one
  action would otherwise have to fully restate every built-in it wants kept, or silently
  lose it. Toggling or gating a built-in stays a single targeted override either way.
- `requiredGroup: <keycloak-group>` is the entire gating surface. The reverse — finer
  gating than group membership — has no path today; a future need has to invent a new
  mechanism, not extend this one.
- report-viewer gains one new inbound header (`X-Auth-Request-Groups`) and Keycloak
  gains one new client protocol mapper; both are wholly owned by this feature and can be
  removed without touching any of the SPA's other functionality.
- Config drift across a rename is possible and silent: during development, an
  inventory's custom-action entry kept the old `requiredRole` key after the chart moved
  to `requiredGroup` (back when gating was still the client-role design described in
  Context — itself already retired, not merely renamed), and Helm's silent-ignore of
  unrecognized map keys turned that into an *ungated* button rather than a render error.
  Accepted rather than guarded against: `requiredGroup` is the only gating key this
  feature has ever shipped as a reachable mechanism, so there's no realistic path for an
  operator to reintroduce `requiredRole` going forward.
- The catalog is read once at process start; a ConfigMap edit needs the pod to restart
  to take effect. The chart's `checksum/actions-configmap` Deployment annotation already
  forces this on every relevant change, but there is no live re-read or TTL snapshot,
  unlike ADR 0034's launchpad catalog.
- Cross-namespace sidecar discovery (ADR 0034's mechanism for third-party contributions)
  is not attempted here. A future need for actions contributed by a chart other than
  report-viewer's own would require that increment.
- `X-Report-Viewer-User-Assertion` is still a symmetric shared secret under the hood,
  same trust class as the invoke token — it protects against the invoke token leaking on
  its own (logs, traces), not against report-viewer's pod or the Secret object itself
  being compromised. The strictly stronger version — Keycloak mints the assertion via
  token exchange/impersonation, targets verify against Keycloak's JWKS like report-viewer
  already does for Path 1 — has no existing precedent to build on (the SPA's invoke calls
  never carry a subject token to exchange) and is deferred until an App needs stronger
  guarantees than report-viewer's own operational trust.

## Alternatives Considered

| Option | Verdict |
| --- | --- |
| Keycloak client roles via `resource_access` (Bearer JWT), gating in the JWT-validation path | Rejected: unreachable from the SPA's own requests (`client.ts` never sends a Bearer token) — a role-gated action could never appear in the real UI for anyone |
| A report-viewer-owned OIDC scope forcing role claims onto the oauth2-proxy session | Rejected: reinvents Keycloak's existing group-membership mapper with more moving parts, for the same header-trust guarantee the group approach gets for free |
| Cross-namespace sidecar ConfigMap discovery (full ADR 0034 parity) | Deferred: every action today ships from report-viewer's own chart; no third-party-contribution use case yet to justify the RBAC/sidecar cost |
| Client-side-only visibility (hide with CSS/JS, no server-side filter) | Rejected outright: the full descriptor, including any secret material, would round-trip to every browser regardless of group membership |
| `invoke_token` as an `ActionDescriptor` field (`Field(exclude=True)`), catalog stays a single ConfigMap | Rejected: `exclude=True` only stops it leaving the process over the API — it would still sit in plaintext in the catalog ConfigMap, readable by anyone with ConfigMap-read RBAC in the namespace. Moved to a Secret-backed volume keyed by action id instead |
| Sign `X-Report-Viewer-User-Assertion` with the same value as `invoke_token` | Rejected: collapses two distinct protections into one — anyone who obtains the invoke token (which travels on every call) could forge any assertion claims they wanted, making the "independent" verification not independent at all |
| No user assertion at all; targets trust `username` in the request body | Rejected: an unsigned string proves nothing: no target-side way to catch a bug in report-viewer's own `requiredGroup` check, and anything holding the invoke token can claim to be any user |
