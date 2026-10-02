# xnat-explore-poc

A reference implementation of a report-viewer `backend-call` search action
(issue #739 / [ADR 0038](../../docs/internal/adr/0038-extensible-report-viewer-search-actions.md)).
It is **not a real XNAT integration** — no XNAT REST calls, no project/subject/experiment
correlation. Its only job is to prove and document the contract a real "Apps" tier target
(issue #595) must implement: verify the caller, resolve the forwarded cohort, and return a
result URL. Read this before building a real backend-call target — see
[docs/source/customize/report-viewer-actions.md](../../docs/source/customize/report-viewer-actions.md)
for the full authoring guide this app implements.

> **On-prem only.** Like the report-viewer action mechanism itself, this reference app
> assumes Traefik + oauth2-proxy fronting report-viewer (ADR 0035). It has no bearing on
> aws-mode deployments — see report-viewer's own on-prem-only warnings in
> `report_viewer/auth.py` and ADR 0038's Known Limitations.

## What it does

`POST /invoke` receives `{search_id, sql, username, reports, cohort_truncated}` from
report-viewer, verifies two independent secrets (below), logs the resolved cohort size,
and returns `{"url": "..."}` pointing at this app's own self-hosted landing page, which
renders `Cohort of N reports received for <user>`. report-viewer then opens that URL the
same way it opens any `open-url` action.

It deliberately runs as **two separate FastAPI apps on two separate ports**, not one app
on two ports:

| App           | Port (default) | Routes                | Reachable from                                                             |
|---------------|----------------|-----------------------|----------------------------------------------------------------------------|
| `invoke_app`  | 8000           | `/invoke`, `/healthz` | report-viewer's own pod only (NetworkPolicy) — never fronted by an Ingress |
| `landing_app` | 8080           | `/`, `/healthz`       | Public, via an Ingress with `Cross-Origin-Opener-Policy: unsafe-none`      |

Splitting them means `/invoke` is *structurally* unreachable from the public listener,
not just NetworkPolicy-restricted — a bug in the NetworkPolicy can't accidentally expose
it. The landing page needs `COOP: unsafe-none` because it's opened as a popup from OWUI's
sandboxed chat embed; see `security-headers-sameorigin-popups` in
`ansible/roles/traefik/tasks/main.yaml` for the full citation. Self-hosting the landing
page (rather than pointing at a real XNAT deployment) means this demo doesn't need a real
XNAT Ingress's COOP header changed just to prove the popup mechanism end to end.

## Security model

`/invoke` requires two independent secrets, both forwarded by report-viewer per-call:

- **`X-Report-Viewer-Action-Token`** (`invokeToken`) — a bearer token proving the caller
  knows a shared secret. Says nothing about *which user* the call is for.
- **`X-Report-Viewer-User-Assertion`** (`assertionKey`) — a short-lived (60s) HS256 JWT,
  signed with a *different* secret, carrying `sub`, `groups`, `search_id`, `action_id`.
  `/invoke` verifies its signature, expiry, that `search_id` matches the request body
  (anti-replay), and — if `REQUIRED_GROUP` is set — that `groups` contains it.

`invokeToken` and `assertionKey` **must be different values** — see
`src/xnat_explore_poc/config.py` and the customize guide's
[Securing a backend-call target](../../docs/source/customize/report-viewer-actions.md#securing-a-backend-call-target)
section for why reusing one for both defeats the point of signing anything.

Neither secret should be a plain values-driven env var in a real deployment; the Helm
chart here sources both from a Kubernetes Secret (`templates/secret.yaml`), not
`values.yaml` directly.

## Configuration

All settings are env vars prefixed `XNAT_EXPLORE_POC_` (see `src/xnat_explore_poc/config.py`):

| Env var                              | Default                 | Notes                                                                          |
|--------------------------------------|-------------------------|--------------------------------------------------------------------------------|
| `XNAT_EXPLORE_POC_PORT`              | `8000`                  | Internal invoke listener.                                                      |
| `XNAT_EXPLORE_POC_LANDING_PAGE_PORT` | `8080`                  | Public landing-page listener.                                                  |
| `XNAT_EXPLORE_POC_LANDING_BASE_URL`  | `http://localhost:8080` | This app's own public base URL — what `/invoke`'s response points at.          |
| `XNAT_EXPLORE_POC_INVOKE_TOKEN`      | `""`                    | Must match report-viewer's `actions.custom[].invokeToken` for this action.     |
| `XNAT_EXPLORE_POC_ASSERTION_KEY`     | `""`                    | Must match `actions.custom[].assertionKey`; must differ from the invoke token. |
| `XNAT_EXPLORE_POC_REQUIRED_GROUP`    | `""`                    | Keycloak group required in the assertion's `groups` claim, or empty to skip.   |

## Local development

```bash
cd examples/xnat-explore-poc
pip install -e '.[dev]'
pytest -v

XNAT_EXPLORE_POC_INVOKE_TOKEN=dev-token \
XNAT_EXPLORE_POC_ASSERTION_KEY=dev-assertion-key \
python -m xnat_explore_poc
```

This starts both listeners (`:8000` for `/invoke`, `:8080` for the landing page). Note
`invokeToken` and `assertionKey` differ, per the security model above.

## Deploying

Built and pushed to `ghcr.io/washu-tag/xnat-explore-poc` by
`.github/workflows/ci.yaml` (image matrix entry `subproject: examples/xnat-explore-poc`).
Deploy the chart in `helm/` directly with `helm install`/`helm upgrade` — this example has
no Ansible role of its own, unlike the components under `ansible/roles/`.

Required `values.yaml` overrides:

- `landingBaseUrl` — this app's own public base URL (bare, no trailing slash).
- `landingPage.ingress.host` — the bare hostname (no scheme) to serve the landing page on.
- `networkPolicy.reportViewerNamespace` — the namespace report-viewer is deployed into.
- `invokeToken`, `assertionKey` — generate two independent random secrets.
- `requiredGroup` — optional, matching whatever `requiredGroup` gates this action on
  report-viewer's side.

Then add a matching entry to report-viewer's own `actions.custom` (see the customize
guide linked above) pointing `endpointUrl` at this chart's in-cluster invoke service.

## Layout

```
src/xnat_explore_poc/
  app.py       # invoke_app + landing_app, the /invoke contract, landing page HTML
  config.py    # env-var settings
  __main__.py  # runs both apps concurrently
tests/         # pytest — invoke auth/validation, landing page rendering
helm/          # deployment.yaml, service + landing-service, ingress, networkpolicy, secret
```
