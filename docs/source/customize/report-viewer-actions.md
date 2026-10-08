# Add or Gate a Report-Viewer Search Action

The search-detail toolbar in report-viewer (Explain Search, Download CSV, and any
site-added buttons) is a data-driven catalog rendered by report-viewer's Helm chart.
Adding a new link or backend-call button, or restricting an existing one to a Keycloak
client role, is a `values.yaml` change plus `helm upgrade` — no report-viewer code
change or image rebuild.

Unlike the [launchpad's chip catalog](launchpad-chips.md), this isn't live runtime
discovery: the catalog is a chart-rendered ConfigMap read once when the report-viewer
pod starts. A `helm upgrade` rolls the pod automatically (the chart hashes the rendered
config into a Deployment annotation), so changes take effect within the normal pod
restart window — seconds, not the launchpad's ~10-second sidecar propagation, but not
instant either.

If report-viewer is deployed through this repo's Ansible role (the normal path — see
`ansible/roles/report_viewer`), configure this from `inventory.yaml` rather than editing
`helm/report-viewer/values.yaml` directly:

```yaml
# inventory.yaml
report_viewer_explain_search:
  requiredRole: report-viewer-admin
report_viewer_download_csv:
  enabled: false
report_viewer_custom_actions:
  - id: pacs-viewer
    title: Open in PACS
    url: https://pacs.example.org/
    requiredRole: report-viewer-admin
```

`report_viewer_explain_search`/`report_viewer_download_csv` are deep-merged onto chart
defaults (set only the field you're changing); `report_viewer_custom_actions` is passed
through as-is (it's a list, so it fully replaces the default empty list, not merged
per-entry). Every field shown below under `actions.*` in this guide is the same field
name whether it's set through one of these inventory variables or directly in
`values.yaml` — the rest of this guide describes the underlying chart shape those
variables render into. See `ansible/inventory.example.yaml` for a commented-out example
block, including the custom-action secrets shape.

```{warning}
**This entire feature — and report-viewer's browser-facing UI in general — is on-prem
only**, though the reason has shifted. Visibility, `requiredRole`, and every button in
the toolbar are gated by a Bearer JWT report-viewer validates against Keycloak; Traefik's
report-viewer-scoped forwardAuth middleware injects that bearer on every request through
the ingress, including the SPA's own, so the auth mechanism itself no longer structurally
depends on Traefik the way the old oauth2-proxy-header path did (ADR 0039's original
limitation). What's still missing is simpler: **report-viewer has no aws-mode Ingress at
all yet** (ADR 0035's Consequences list only Superset and Keycloak as landed there). If
you're deploying report-viewer in aws mode, nothing here will be reachable at all — not
because the auth check can't work, but because there's no ingress path for aws-mode
traffic to reach report-viewer in the first place. Closing that gap would need its own
aws-mode Ingress plus wiring `forwarded_token_header` to ALB's forwarded token
(`REPORT_VIEWER_FORWARDED_TOKEN_HEADER`), which Path 1 already supports — undesigned,
not attempted here.
```

## Toggling and gating the built-in buttons

Explain Search and Download CSV are always in the catalog unless disabled:

```yaml
actions:
  explainSearch:
    enabled: true
    requiredRole: '' # e.g. report-viewer-admin
  downloadCsv:
    enabled: true
    requiredRole: ''
```

Set `enabled: false` to remove a button entirely, or `requiredRole` to restrict it to
callers holding that Keycloak client role (empty means visible to every authenticated
user). These are objects, not plain booleans, specifically so you can override just one
field — Helm deep-merges map values, so setting `downloadCsv.requiredRole` doesn't
require restating `explainSearch` or anything in `custom` below.

## Adding a new button: `actions.custom`

`actions.custom` is a list of site-authored entries. Two shapes are supported:

### `open-url` — a static link

```yaml
actions:
  custom:
    - id: pacs-viewer
      title: Open in PACS
      url: https://pacs.example.org/
      weight: 50
      requiredRole: report-viewer-admin
```

| Field          | Required | Default | Notes                                                                               |
|----------------|----------|---------|--------------------------------------------------------------------------------------|
| `id`           | yes      | —       | Duplicate ids (against a built-in or another custom entry) reject the later one.      |
| `title`        | yes      | —       | Button label.                                                                         |
| `url`          | yes      | —       | Must be `http(s)` with a real host — `javascript:`/`data:` and similar are rejected.  |
| `weight`       | no       | `100`   | Lower renders first; ties break by title, then id.                                    |
| `requiredRole` | no       | —       | Keycloak client role required to see the button (see [Gating](#gating-with-requiredrole)). |

The SPA opens `url` via a real navigation, with an always-shown copy-link fallback —
see [Popups from the chat embed](#popups-from-the-chat-embed) if the destination needs
to open as a popup from OWUI's embedded chat.

### `backend-call` — hand off to a separate service

```yaml
actions:
  custom:
    - id: explore-xnat
      title: Explore in XNAT
      actionType: backend-call
      endpointUrl: http://your-app.<namespace>.svc.cluster.local:8000/invoke
      invokeToken: <a generated secret>
      requiredRole: report-viewer-admin
```

| Field                                    | Required | Default | Notes                                                                                                               |
|------------------------------------------|----------|---------|---------------------------------------------------------------------------------------------------------------------|
| `id`, `title`, `weight`, `requiredRole`  | —        | —       | Same as `open-url` above.                                                                                           |
| `endpointUrl`                            | yes      | —       | POSTed to when the button is clicked. Must be `http(s)` with a real host.                                           |
| `invokeToken`                            | no       | —       | Forwarded as `X-Report-Viewer-Action-Token`. See [Securing a backend-call target](#securing-a-backend-call-target). |

report-viewer POSTs `{search_id, sql, username, reports, cohort_truncated}` to
`endpointUrl` — `reports` is the resolved cohort as
`{primary_report_identifier, accession_number}` pairs, not just the raw SQL, capped at
the same row limit the SPA itself uses (`REPORT_VIEWER_MAX_COHORT_ROWS`, 50000 by
default). `cohort_truncated` is `true` when the real cohort is larger than that cap —
`reports` is a prefix, not the complete set, and your service should account for that
(e.g. surface it to the user, or fail rather than silently act on a partial cohort)
rather than assuming `reports` is always exhaustive. Your service returns
`{"url": "..."}`, and the SPA opens it the same way as an `open-url` action.
report-viewer never inspects what your service actually does with the cohort.

`reports` reflects whatever the SPA is currently showing, not necessarily the whole
saved search: if the user has active client-side filters on the results grid, the SPA
sends only the currently-visible rows' ids, and report-viewer intersects that against
the real (Trino-resolved) cohort before forwarding — an id the SPA submits that isn't
actually part of the search is silently dropped, never trusted on its own. Clicking a
backend-call button always means "these studies" — whatever's currently filtered — the
same scope Download CSV already exports, not "the entire search" regardless of what's
on screen.

There is no values field for a `client`-type action (page-specific frontend logic, like
Download CSV) — that needs a handler already registered in report-viewer's own
frontend, so it structurally can't be added through chart values.

## Securing a backend-call target

`invokeToken` is the only credential report-viewer sends, and the only one your service
needs to check — reject any request that doesn't present the correct value as
`X-Report-Viewer-Action-Token`. It proves your service is being called by *something*
that knows the shared secret, nothing more; it doesn't carry identity or role
information, and your service isn't expected to independently re-verify either.

That's sufficient specifically because of where this endpoint should live, not because
the secret itself is strong: **your invoke endpoint must be structurally unreachable
except from report-viewer's own pod** — no public Ingress on that port, and a
NetworkPolicy restricting ingress to report-viewer's pod selector (see
`examples/xnat-explore-poc/helm/values.yaml`'s `networkPolicy` block for the pattern:
namespace + pod-label selector, not just a namespace-wide allow). Given that, the
realistic way `invokeToken` alone could be misused — it leaking in isolation (logs,
traces, a support bundle) to someone who *doesn't* also have a network path to your
endpoint — doesn't actually grant an attacker anything, since the secret alone can't
open a connection NetworkPolicy wouldn't otherwise block. report-viewer already enforces
`requiredRole` before ever calling your endpoint
(`scout_report_viewer.actions.list_actions`); as long as your endpoint is genuinely
unreachable from anywhere else, there's no independent check left for your service to
usefully add.

**What this boundary does and doesn't guarantee.** The invoke token plus your
NetworkPolicy together constrain *who* can reach your endpoint at all — they say nothing
about the specific content of a given request beyond "this came from something that
could reach this endpoint with the right token," which in a correctly-isolated
deployment means report-viewer itself. Report-viewer resolves the `reports` list from
Trino server-side before sending it (`invoke_search_action`'s `visible_report_ids`
handling only ever narrows that resolved cohort, never adds to it) — this contract
doesn't add any further verification on top of trusting that resolution. If your target
needs a guarantee stronger than "report-viewer's own resolution and role check are
correct and its invoke endpoint is genuinely network-isolated," query Trino directly
instead of trusting this payload.

`xnat-explore-poc` (`examples/xnat-explore-poc/`, including its `helm/` subdirectory) is a
reference implementation of all of this: a deliberately fake backend that checks the
shared token and nothing else, purely to demonstrate the contract. Read it before
building a real target.

## Gating with `requiredRole`

`requiredRole` filters what report-viewer's API returns — a caller without the role gets
a `GET /api/searches/{id}/actions` response that simply never includes the button, and
invoking a gated action you can't see 404s the same way a nonexistent one would. This is
server-side filtering, checked against the caller's real Keycloak client role
(`resource_access.report-viewer.roles` on the caller's Bearer JWT — Traefik's
report-viewer-scoped forwardAuth middleware injects a Keycloak-issued bearer on every
request through the ingress, including the SPA's own, so a client cannot forge this
claim by setting a header directly).

For `backend-call` actions specifically, this filter is also the actual enforcement
point, not just UX: `invoke_search_action` re-runs the same `requiredRole` check before
ever calling your endpoint, so a caller who can't see the button can't invoke it either.
Your service doesn't need to re-check roles itself — see
[Securing a backend-call target](#securing-a-backend-call-target) for what it should
check instead.

## Popups from the chat embed

An `open-url` (or `backend-call`) target that needs to open as a popup/new tab from
report-viewer's own OWUI-embedded chat context has one specific requirement: it must
serve `Cross-Origin-Opener-Policy: unsafe-none`. Per the WHATWG HTML navigation
algorithm, a sandboxed iframe without `allow-popups-to-escape-sandbox` (which is how
OWUI embeds report-viewer) can only open a popup on a destination whose COOP is exactly
`unsafe-none` — anything else, including a seemingly-relaxed value like
`same-origin-allow-popups`, is blocked identically to a strict `same-origin`. See
`security-headers-sameorigin-popups` in `ansible/roles/traefik/tasks/main.yaml` for the
full citation and the accepted tradeoff (loses COOP's cross-origin process isolation;
reverse-tabnabbing is separately mitigated regardless, since the SPA always opens links
with `rel="noopener noreferrer"`).

If your destination can't set this header, don't worry about it: the SPA always shows a
copy-link fallback alongside the popup attempt, so the action still works — the user
just has to paste the link themselves instead of a new tab opening automatically.

## Troubleshooting

The action catalog degrades gracefully — one bad entry never blanks the whole toolbar.
Checks, in the order problems actually occur:

1. **Unparseable catalog file** (shouldn't happen from chart-rendered values, but would
   indicate a chart bug) — the entire catalog falls back to the two built-in defaults.
2. **An entry missing required fields, or with an unsafe `url`/`endpointUrl`** — that
   entry is skipped and logged; the rest of the catalog still renders.
3. **Duplicate `id`** (against a built-in or another custom entry) — the later entry is
   skipped and logged.
4. **Every action disabled** (`explainSearch.enabled: false`, `downloadCsv.enabled:
   false`, empty `custom`) — renders a genuinely empty toolbar, not a fallback to
   defaults; this is intentional, so a site can fully replace the toolbar.

Check report-viewer's logs (`kubectl logs -n scout-analytics deploy/report-viewer`,
also in Grafana → Explore → Loki) for skip/validation messages, and confirm the
rendered ConfigMap matches what you expect:

```bash
kubectl get configmap -n scout-analytics report-viewer-actions -o jsonpath='{.data.catalog\.yaml}'
```

## Reference

- Design and rationale: ADR 0039 in the Scout repository
  (`docs/internal/adr/0039-extensible-report-viewer-search-actions.md`).
