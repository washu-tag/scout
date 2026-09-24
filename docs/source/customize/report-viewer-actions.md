# Add or Gate a Report-Viewer Search Action

The search-detail toolbar in report-viewer (Explain Search, Download CSV, and any
site-added buttons) is a data-driven catalog rendered by report-viewer's Helm chart.
Adding a new link or backend-call button, or restricting an existing one to a Keycloak
group, is a `values.yaml` change plus `helm upgrade` — no report-viewer code change or
image rebuild.

Unlike the [launchpad's chip catalog](launchpad-chips.md), this isn't live runtime
discovery: the catalog is a chart-rendered ConfigMap read once when the report-viewer
pod starts. A `helm upgrade` rolls the pod automatically (the chart hashes the rendered
config into a Deployment annotation), so changes take effect within the normal pod
restart window — seconds, not the launchpad's ~10-second sidecar propagation, but not
instant either.

```{warning}
**This entire feature — and report-viewer's browser-facing UI in general — is on-prem
only.** Visibility, `requiredGroup`, and every button in the toolbar depend on
report-viewer's oauth2-proxy/Traefik forwardAuth header path, which does not exist in
aws-mode clusters (ADR 0035: no Traefik, ALB-native OIDC instead, no per-group gate).
If you're deploying report-viewer in aws mode, none of this will authenticate at all —
not just gated buttons, the whole embedded cohort-browsing UI. ADR 0037 records why this
mechanism was built this way; it does not track whether an aws-mode edge has since been
added; check current deployment docs for that.
```

## Toggling and gating the built-in buttons

Explain Search and Download CSV are always in the catalog unless disabled:

```yaml
actions:
  explainSearch:
    enabled: true
    requiredGroup: '' # e.g. scout-admin
  downloadCsv:
    enabled: true
    requiredGroup: ''
```

Set `enabled: false` to remove a button entirely, or `requiredGroup` to restrict it to
members of that Keycloak group (empty means visible to every authenticated user). These
are objects, not plain booleans, specifically so you can override just one field — Helm
deep-merges map values, so setting `downloadCsv.requiredGroup` doesn't require restating
`explainSearch` or anything in `custom` below.

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
      requiredGroup: scout-admin
```

| Field           | Required | Default | Notes                                                                                 |
|-----------------|----------|---------|---------------------------------------------------------------------------------------|
| `id`            | yes      | —       | Duplicate ids (against a built-in or another custom entry) reject the later one.      |
| `title`         | yes      | —       | Button label.                                                                         |
| `url`           | yes      | —       | Must be `http(s)` with a real host — `javascript:`/`data:` and similar are rejected.  |
| `weight`        | no       | `100`   | Lower renders first; ties break by title, then id.                                    |
| `requiredGroup` | no       | —       | Keycloak group required to see the button (see [Gating](#gating-with-requiredgroup)). |

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
      assertionKey: <a second, different generated secret>
      requiredGroup: scout-admin
```

| Field                                    | Required | Default | Notes                                                                                                               |
|------------------------------------------|----------|---------|---------------------------------------------------------------------------------------------------------------------|
| `id`, `title`, `weight`, `requiredGroup` | —        | —       | Same as `open-url` above.                                                                                           |
| `endpointUrl`                            | yes      | —       | POSTed to when the button is clicked. Must be `http(s)` with a real host.                                           |
| `invokeToken`                            | no       | —       | Forwarded as `X-Report-Viewer-Action-Token`. See [Securing a backend-call target](#securing-a-backend-call-target). |
| `assertionKey`                           | no       | —       | Signs `X-Report-Viewer-User-Assertion`. Must be a **different** value from `invokeToken` — see below.               |

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

`invokeToken` alone only proves your service is being called by *something* that knows
the shared secret — it says nothing about which user the invocation is for, and
therefore can't tell you whether report-viewer's own `requiredGroup` check is actually
working. Treat it as a bare minimum: reject any request that doesn't present the
correct token, but don't stop there.

If you set `assertionKey`, report-viewer also sends `X-Report-Viewer-User-Assertion`: a
short-lived (60 second) JWT, signed with `assertionKey` using HS256, carrying:

```text
{ "sub": "<username>", "groups": ["<group>", ...], "search_id": "<id>", "action_id": "<id>", "iat": <unix ts>, "exp": <unix ts> }
```

Your service should verify, independently of anything report-viewer already checked:

1. **Signature** — decode with your copy of `assertionKey`.
2. **Expiry** — reject if `exp` has passed.
3. **`search_id`** — must match the `search_id` in the request body, so a captured
   assertion can't be replayed against a different search within its validity window.
4. **`groups`** — if your action should be restricted, check group membership here too,
   rather than assuming report-viewer's `requiredGroup` already enforced it. A hidden
   button's URL is not itself a secret.

`invokeToken` and `assertionKey` **must be different values**. `invokeToken` is
transmitted on every call and can leak via logs, traces, or a support bundle;
`assertionKey` never travels over the wire — only its signature output does, which
can't be reversed to recover it. Reusing one value for both would let anyone who
obtained the (much more exposed) invoke token forge whatever user or group claims they
wanted, defeating the point of signing anything at all. Generate both as independent
random secrets and store them as real Kubernetes Secrets on your service's side, not
plain values or Deployment env literals — the same reasoning applies to your service as
to report-viewer's own chart.

`xnat-explore-poc` (`examples/xnat-explore-poc/`, including its `helm/` subdirectory) is a reference
implementation of all of this: a deliberately fake backend that verifies the token, the
assertion, and an optional required group, purely to demonstrate the contract. Read it
before building a real target.

## Gating with `requiredGroup`

`requiredGroup` filters what report-viewer's API returns — a non-member's response from
`GET /api/searches/{id}/actions` simply never includes the button, and invoking a
gated action you can't see 404s the same way a nonexistent one would. This is
server-side filtering, checked against the caller's real Keycloak group membership
(delivered via oauth2-proxy's `X-Auth-Request-Groups` header, which Traefik overwrites
from its own verified session on every request — a client cannot forge it by setting
the header directly).

That said, visibility is UX, not the authorization boundary: a `backend-call` action's
own endpoint is a real, independently reachable service, and must enforce its own check
too (see [Securing a backend-call target](#securing-a-backend-call-target)) — a hidden
button's URL was never a secret in the first place.

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

- Design and rationale: ADR 0037 in the Scout repository
  (`docs/internal/adr/0037-extensible-report-viewer-search-actions.md`).
