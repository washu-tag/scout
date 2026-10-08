# ADR 0039: Extensible Report-Viewer Search Actions

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

### The invoke boundary relies on network isolation, not a signed assertion

`X-Report-Viewer-Action-Token` proves only that the caller knows a shared secret — it
says nothing about which end user the call is for or what they're authorized to do. An
earlier version of this design added a second mechanism on top of it:
`X-Report-Viewer-User-Assertion`, a short-lived HS256 JWT signed with a separate
per-action key, carrying `sub`/`roles`/`search_id` so a target could independently
verify identity and role membership rather than trusting the shared secret alone. A PR
reviewer raised that this amounted to report-viewer minting its own tokens, and asked
whether Keycloak could do this instead (its "whole deal").

Investigating that question changed the design rather than just answering it. Keycloak
token exchange could replace the self-signing with a real Keycloak-issued token for
identity/role — but it has no mechanism to embed `search_id` or bind the `reports`
payload into an exchanged token either, since those are report-viewer-specific concepts
Keycloak has no notion of; closing that gap would need a custom Keycloak SPI, which
carries the same bug surface as the HS256 code it would replace. So token exchange
wouldn't actually simplify anything — it would just relocate the same custom signing
logic into Keycloak's process instead of report-viewer's.

That reframed the real question: given a bug in report-viewer's own role-derivation
would be faithfully signed by *either* approach (a signature only proves the message
wasn't altered in transit, not that its contents are correct), what is the assertion
actually defending against? The answer is narrower than it first appears: specifically,
someone who obtains the invoke token in isolation (it travels on every `/invoke` call
and can leak via logs or traces) without also compromising report-viewer's own pod or
process. But `/invoke` is already required to be structurally unreachable except from
report-viewer's own pod — no public Ingress, NetworkPolicy restricted to report-viewer's
pod selector (see the Known Limitations entry above). Reaching it at all requires a
network identity NetworkPolicy already treats as report-viewer; a leaked secret alone
grants no such path. And anyone who *does* have that network-level access would, in
practice, also have access to any co-located assertion key, since both secrets are
mounted from the same Kubernetes Secret on report-viewer's side. The scenario the
assertion was built to catch doesn't survive contact with how this is actually deployed.

So the assertion mechanism (`mint_user_assertion`, `X-Report-Viewer-User-Assertion`, the
`assertionKey`/`<action_id>.assertion-key` secret, and the target-side signature/expiry/
`search_id`/role checks it required) was removed outright rather than replaced. The
invoke token is the only credential now: it authenticates "a caller who can reach this
endpoint at all," which — given a correctly-configured NetworkPolicy — already means
report-viewer, and report-viewer already enforces `requiredRole` before ever calling out
(`list_actions`, re-run inside `invoke_search_action`). A target isn't expected to
independently re-verify role membership; there's no signed claim left to check it
against, and nothing in the surviving threat model needs one. This directly answers the
original review concern by elimination: report-viewer no longer mints any tokens at all,
and token exchange — which only ever would have addressed the identity half, not the
`reports`-binding gap — is no longer relevant either.

`xnat-explore-poc`, as the reference implementation, demonstrates exactly this reduced
contract: it checks the shared token and nothing else.

### Visibility gates on Keycloak client roles, delivered on a Bearer JWT

`list_actions(user_roles)` filters the catalog server-side. For `backend-call` actions
this filter is also the actual enforcement point, not just UX — `invoke_search_action`
re-runs it before ever calling the target's endpoint, so a caller who can't see a button
can't invoke it either (see the invoke-boundary section above for why the target itself
doesn't need to independently re-check).

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
`claim.name=groups`), `oidc_groups_claim`, and `X-Auth-Request-Groups` itself were all
introduced by this effort for the groups-based design and never had any other consumer —
confirmed by diff against `origin/main`, where none of the three existed at all. All
three were removed outright rather than left in place once that was confirmed.

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
  removed without touching any of the SPA's other functionality.
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
- The invoke boundary relies entirely on the shared invoke token plus network isolation
  (see above) — it does not cryptographically bind the request body: nothing ties the
  `reports` payload to a specific invocation beyond trusting report-viewer's own
  resolution and that its invoke endpoint is genuinely unreachable except from
  report-viewer's pod. An App needing a stronger guarantee than that needs to query
  Trino directly rather than trust this payload.
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
| Self-signed HS256 `X-Report-Viewer-User-Assertion`, independent of `invoke_token` | Superseded: implemented first, removed after review revealed the scenario it defended against (the invoke token leaking in isolation, without report-viewer's own pod also being compromised) doesn't survive the invoke endpoint's required network isolation — see the invoke-boundary section above |
| Sign the assertion with the same value as `invoke_token` (while the assertion above still existed) | Rejected: collapses two distinct protections into one — anyone who obtains the invoke token (which travels on every call) could forge any assertion claims they wanted, making the "independent" verification not independent at all |
| No user assertion at all; targets trust the shared invoke token and an unsigned `username` in the request body | **Accepted**, after the assertion above was removed: sufficient specifically because the invoke endpoint is required to be unreachable except from report-viewer's own pod, and report-viewer already enforces `requiredRole` before ever calling out — see the invoke-boundary section above |
| Keycloak-minted assertion via token exchange, instead of self-signed HS256 | Moot: investigating this revealed standard token exchange can't bind `search_id`/`reports` into the issued token either (the same gap, just relocated into a custom Keycloak SPI with the same bug surface) — that's what led to removing the assertion mechanism entirely rather than replacing it. A prior attempt at a similar pattern elsewhere (XNAT auth, PR #410) was abandoned without merging |
