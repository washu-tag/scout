# ADR 0038: Extensible Report-Viewer Search Actions

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
surfaced a fact the design had gotten wrong: report-viewer had two inbound auth paths
(ADR 0029) — a Bearer JWT, used only by OWUI's server-side tool calls, and an
oauth2-proxy-forwarded header, used by every request the SPA's own frontend made.
Reading `frontend/src/api/client.ts`'s `api()` function confirmed it never attached an
`Authorization` header — every fetch the browser made took the header path, which
carried no role or group claim at all. A role-gated action could therefore never appear
in the real product UI, for anyone, no matter how correctly Keycloak was configured: the
JWT path it depended on wasn't reachable from anything that rendered a toolbar. Gating
moved to Keycloak group membership instead, delivered over that header path.

A separate effort later closed the actual gap: oauth2-proxy's `set_authorization_header`
option, paired with a report-viewer-scoped Traefik middleware (not the shared one every
other service uses), makes Traefik inject a real Keycloak-issued Bearer JWT on *every*
request through report-viewer's ingress — including the SPA's own, server-side, with no
frontend code change. That reopened client-role gating as a real option rather than the
dead end it had been, and this ADR documents that as the final design: gating is back on
Keycloak client roles via `resource_access`, Path 2 (the oauth2-proxy header path and its
`X-Report-Viewer-Gateway` secret) is retired, and Bearer JWT is report-viewer's only
inbound auth path.

## Decision

Two decisions, layered on top of each other.

### Actions are declared data, rendered generically

`ActionDescriptor` (`report-viewer/src/scout_report_viewer/actions.py`): `id`, `title`,
`weight`, `action_type`, `url`, `required_role`, `client_handler`, `endpoint_url`. A
`backend-call` entry's invoke token is deliberately not a field on this model at all —
see below. The chart
renders today's two built-in buttons — each independently toggleable and role-gateable
via its own `actions.explainSearch`/`actions.downloadCsv` object (`enabled`,
`requiredRole`) — plus any site-admin-authored `actions.custom` entries, into a
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
  reports or be absent), optionally intersected against a caller-supplied
  `visible_report_ids` (the SPA's currently client-side-filtered rows, the same set
  Download CSV already exports — a submitted id outside the resolved cohort is silently
  dropped, never trusted on its own), and forwards those alongside the raw `sql` to
  `endpoint_url` (a
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
  (`examples/xnat-explore-poc/`, including its `helm/` subdirectory) is the reference implementation: a
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
`requiredRole` check never having a bug, and anything that obtains the token (a log
line, a captured trace, another compromised in-cluster workload) can invoke the action
as any user with any cohort. This directly contradicts the framing above — "visibility
is UX, not the authorization boundary... a real action must still independently enforce
the same role check at its own endpoint" — unless the target actually has something to
check.

So `invoke_search_action` also mints `X-Report-Viewer-User-Assertion`: a short-lived
(60s) HS256 JWT carrying `sub`, `roles`, `search_id`, and `action_id` — enough for a
target to independently check identity and role membership, plus a `search_id` match
against the request body that catches a *naive* replay (reusing a captured
assertion+body wholesale against a different search without updating that field). It is
not a guarantee against a deliberate one: both the token's `search_id` and the body's are
visible to, and settable by, anyone holding a captured assertion, so this alone doesn't
stop someone from keeping `search_id` matched while substituting a different `reports`
list. Nothing in this contract cryptographically binds `reports` — the actual cohort
payload — to the assertion at all. That gap is reachable only by bypassing report-viewer
entirely (network access to the target plus a valid invoke token plus a captured
assertion); it is not exposed through report-viewer's own UI, which never trusts
client-supplied report ids (`invoke_search_action`'s `visible_report_ids` only narrows
report-viewer's own Trino-resolved cohort, never adds to it). A target wanting a
stronger guarantee than "report-viewer's own resolution and role check are correct"
needs to query Trino directly rather than trust this payload.

The signing key is a **second, separate** per-action secret
(`<action_id>.assertion-key` in the same Secret-backed volume as the invoke token, set
via `actions.custom[].assertionKey`) — never the invoke token itself. Reusing the invoke
token as the signing key would mean anyone who obtained it (which travels on every
`/invoke` call and can leak via logs or traces) could also forge arbitrary user/role
claims, defeating the entire point of a signature the caller couldn't otherwise produce.
The two secrets have different exposure profiles: the invoke token is transmitted on the
wire on every call, while the assertion key never is — only its signature output goes
out, which can't be reversed to recover the key. This protects against the invoke token
leaking via an ordinary operational mistake; it does not protect against report-viewer's
own pod or the Secret object itself being compromised, which exposes everything
regardless of how many distinct values exist.

`xnat-explore-poc`, as the reference implementation, actually verifies this rather than
trusting the shared secret alone: signature and expiry via `assertionKey`, `search_id`
bound to the current request, and an optional `requiredRole` membership check against
the asserted `roles` — demonstrating what a real App is expected to do, not just what
report-viewer sends.

### Visibility gates on Keycloak client roles, delivered on a Bearer JWT

`list_actions(user_roles)` filters the catalog server-side. Visibility is UX, not the
authorization boundary (ADR 0034's framing, unchanged here) — a real `backend-call`
action must still enforce its own check independently, since a hidden action's URL is
not itself a secret.

A dedicated bearer-only `report-viewer` Keycloak client (no OAuth flow of its own — it
exists solely to own a role namespace) carries the `report-viewer-admin` client role.
A `report-viewer-roles-mapper` on the existing `report-viewer-audience` client scope
delivers it into `resource_access.report-viewer.roles` on any token that scope is
attached to — `oauth2-proxy`'s client and `open-webui`'s client both already carry that
scope (for `aud=report-viewer`), so both the SPA's own requests and OWUI's server-side
tool calls can carry the role claim.

What makes the SPA's own requests reachable at all: oauth2-proxy's
`set_authorization_header` option emits a real Keycloak-issued ID token on `/oauth2/auth`
responses, and a report-viewer-scoped Traefik middleware
(`oauth2-proxy-auth-report-viewer`, *not* the shared `oauth2-proxy-auth` every other
service uses) adds `Authorization` to what it copies onto the upstream request. Every
request through report-viewer's ingress — including the SPA's own browser fetches, which
never set this header themselves — arrives carrying a real Bearer JWT. Two live bugs had
to be fixed to make this actually work, not just in theory: the `report-viewer-audience`
mapper originally only stamped `aud=report-viewer` onto access tokens
(`id.token.claim: false`) — oauth2-proxy forwards the ID token, not the access token, so
the audience check failed until that was flipped to `true`. And python-jose rejected the
ID token's `at_hash` claim (binding it to its paired access token, which report-viewer
never has) until `_validate_jwt` was told to skip that check — report-viewer already
verifies signature/exp/iss/aud independently, so the binding adds nothing it needs.

`auth.py` has a single inbound auth path now: Bearer JWT. The oauth2-proxy-header path
(`X-Auth-Request-Preferred-Username`/`X-Auth-Request-Groups`, gated by a
`X-Report-Viewer-Gateway` shared secret) existed only because the SPA's own requests had
no other way to carry an authorization claim; once they could, it was retired along with
the secret and its Traefik middleware. `forwarded_token_header` (the aws-mode ALB path)
is unaffected — it was already a way to get a token into this same Bearer path, not a
separate one.

The `oauth2-proxy` client's own `groups-mapper` (`oidc-group-membership-mapper`,
`claim.name=groups`) is untouched — other services still consume
`X-Auth-Request-Groups` off the shared `oauth2-proxy-auth` middleware. Retiring groups
here is report-viewer-local; the realm's group plumbing for everyone else is unaffected.

## Consequences

- Adding, removing, or gating an `open-url`/`backend-call` button is a `values.yaml`
  change (`actions.custom`) plus `helm upgrade` — no report-viewer code change or image
  rebuild. `client` actions still need a source change; there is structurally no values
  field for one.
- `explainSearch`/`downloadCsv` are objects (`enabled`, `requiredRole`), not plain
  booleans, deliberately kept out of `actions.custom` — Helm deep-merges map values but
  replaces list values wholesale, so a site overriding `actions.custom` to add one
  action would otherwise have to fully restate every built-in it wants kept, or silently
  lose it. Toggling or gating a built-in stays a single targeted override either way.
- `requiredRole: <keycloak-client-role>` is the entire gating surface. The reverse —
  finer gating than a single role — has no path today; a future need has to invent a new
  mechanism, not extend this one.
- report-viewer gains a dedicated Keycloak client (`report-viewer`, bearer-only, owns the
  role namespace) and a second protocol mapper on `report-viewer-audience`
  (`report-viewer-roles-mapper`); both are wholly owned by this feature and can be
  removed without touching any of the SPA's other functionality. Nothing new is added to
  the realm's shared `oauth2-proxy-auth` middleware or `groups-mapper` — those stay
  exactly as every other service already depends on them.
- Helm silently ignores unrecognized map keys, so a typo'd or stale gating key (e.g. a
  leftover `requiredGroup` from before this reversal) renders an *ungated* button rather
  than a config error, not a loud failure. Worth checking the rendered ConfigMap
  (`kubectl get configmap <release>-actions -o yaml`) after any gating change, not just
  assuming the values diff did what was intended.
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
  being compromised. It also doesn't bind the request body: `search_id` matching catches
  a naive replay, not a deliberate one, and nothing here cryptographically ties the
  `reports` payload to the assertion at all — see the invoke-boundary section above for
  what this does and doesn't guarantee. Replacing it with Keycloak-minted tokens (token
  exchange, verified against Keycloak's JWKS like Path 1 already is) is under active
  discussion but not decided: a prior attempt at a similar pattern for a different
  integration (XNAT auth, PR #410) was abandoned without merging, and token exchange
  alone wouldn't close the request-body-binding gap either — standard token exchange has
  no mechanism for embedding request-specific data like `search_id` into the issued
  token, so that part of the gap is orthogonal to which party signs the token. Deferred
  until an App needs a stronger guarantee than report-viewer's own operational
  correctness.
- `xnat-explore-poc` lives under `examples/xnat-explore-poc/` (source and chart together,
  the chart in a nested `helm/`), matching the `examples/` convention issue #595's own
  reference implementation (`examples/pluggable-app/`, a different Pluggable Apps tier —
  a front-door app with its own Keycloak client and launchpad chip, not a backend-call
  target) established. Both live as siblings under `examples/`, excluded from e2e as
  reference material rather than deployed components.

## Known Limitations

**This entire feature is on-prem only**, though the reason has changed shape.
report-viewer's single inbound path is now a Bearer JWT, validated the same way
regardless of how the token arrives — on-prem, Traefik's report-viewer-scoped forwardAuth
middleware injects one from oauth2-proxy on every request, including the SPA's own; in
principle, aws mode's ALB could feed the same path via `forwarded_token_header`
(`REPORT_VIEWER_FORWARDED_TOKEN_HEADER`, pointed at `X-Amzn-Oidc-Accesstoken`), since
`oauth2-proxy`'s client already carries the `report-viewer-audience` scope the ALB's
reused client would need too. What's actually missing is simpler than the old
Traefik-dependency story: **report-viewer has no aws-mode Ingress at all yet** (ADR
0035's Consequences list only Superset and Keycloak as landed so far). Nothing routes
aws-mode traffic to report-viewer in the first place, so the question of whether its auth
path would work there is untested, not just unbuilt.

Closing this gap is real, undesigned work, not a small tweak: an aws-mode Ingress for
report-viewer, wiring `forwarded_token_header`, and confirming the `report-viewer-roles-mapper`
delivers `resource_access.report-viewer.roles` onto whatever token ALB forwards (the same
class of "verify before trusting" bug this effort already hit twice for the on-prem
path — the audience mapper and `at_hash` fixes described above). Until then, report-viewer
(and everything in this ADR) should be treated as on-prem-only, the same caveat PR #755's
`examples/pluggable-app` carries for its own Traefik/oauth2-proxy dependency.

**A window opened from OWUI's sandboxed chat iframe inherits its sandbox flags, not
just whatever lets it open at all.** `allow-popups` alone governs whether
`window.open()`/an anchor click can open a new tab from a sandboxed context — it says
nothing about what that tab can then do, since the HTML sandboxing spec propagates the
opener's flags to the new browsing context unless the opener sets
`allow-popups-to-escape-sandbox` (which OWUI's iframe does not). Verified against a real
embedded popup (`examples/xnat-explore-poc`'s landing page, opened the same way "Explore
in XNAT" does) rather than by spec-reading alone:

- `allow-top-navigation` (the unconditional form, not just
  `allow-top-navigation-by-user-activation`) is present — a target app's own
  navigation/redirects work normally, including after an async gap (e.g. a fetch, an
  SSO hop), not only inside the original click.
- `allow-popups` is present — a target app can open further popups of its own.
- `allow-forms` is present — a real `<form>` submission (not just script/anchor-driven
  navigation, which `allow-top-navigation` covers separately) completes normally.
- `allow-modals` is **absent** — `window.alert()`/`confirm()`/`prompt()` are silently
  swallowed (`Ignored call to 'alert()'. The document is sandboxed, and the
  'allow-modals' keyword is not set.`). Any real target app that gates a destructive
  action behind `confirm()`, or surfaces an error via `alert()`, will appear to do
  nothing when the user clicks — the same "looks hung, nothing happened" failure shape
  as a silently-blocked action, just a different root cause. XNAT itself uses native
  dialogs in some of its own flows, so this is a live risk for any future action that
  opens real XNAT (today's `xnat-explore-poc` demo doesn't exercise XNAT's own UI, so it
  hasn't surfaced there).

Not something Scout controls or can route around from report-viewer's side — the fix is
OWUI's iframe adding `allow-popups-to-escape-sandbox` to its own `sandbox` attribute, a
change in OWUI's embedding, not in this repo.

## Alternatives Considered

| Option | Verdict |
| --- | --- |
| Keeping Keycloak group membership (the design this ADR shipped with first) | Superseded: it existed only because the SPA's own requests had no way to carry a Bearer JWT. Once Traefik could inject one on every request (739-bearer-token-spike), the reason to prefer groups over client roles — Scout's normal per-service role-vocabulary pattern — no longer applied |
| Cross-namespace sidecar ConfigMap discovery (full ADR 0034 parity) | Deferred: every action today ships from report-viewer's own chart; no third-party-contribution use case yet to justify the RBAC/sidecar cost |
| Client-side-only visibility (hide with CSS/JS, no server-side filter) | Rejected outright: the full descriptor, including any secret material, would round-trip to every browser regardless of role membership |
| `invoke_token` as an `ActionDescriptor` field (`Field(exclude=True)`), catalog stays a single ConfigMap | Rejected: `exclude=True` only stops it leaving the process over the API — it would still sit in plaintext in the catalog ConfigMap, readable by anyone with ConfigMap-read RBAC in the namespace. Moved to a Secret-backed volume keyed by action id instead |
| Sign `X-Report-Viewer-User-Assertion` with the same value as `invoke_token` | Rejected: collapses two distinct protections into one — anyone who obtains the invoke token (which travels on every call) could forge any assertion claims they wanted, making the "independent" verification not independent at all |
| No user assertion at all; targets trust `username` in the request body | Rejected: an unsigned string proves nothing: no target-side way to catch a bug in report-viewer's own `requiredRole` check, and anything holding the invoke token can claim to be any user |
| Keycloak-minted assertion via token exchange, instead of self-signed HS256 | Deferred, not rejected: closes the "this service mints its own tokens" concern for identity/role, but doesn't close the request-body-binding gap either (standard token exchange has no mechanism for embedding `search_id` into the issued token), and a prior attempt at a similar pattern elsewhere (PR #410, XNAT auth) was abandoned without merging. See the invoke-boundary Consequences entry above |
