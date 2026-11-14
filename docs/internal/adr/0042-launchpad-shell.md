# ADR 0042: Launchpad Shell for Scout Services

**Date:** 2026-11
**Status:** Accepted
**Decision Owner:** TAG Team

## Context

Scout's services each run on their own subdomain: chat, analytics, notebooks,
monitoring, XNAT, report-viewer, and any Pluggable App. Before this decision the
launchpad (ADR 0034) was a page of tiles. Each tile opened its service in a new tab.
Moving between services meant juggling tabs that looked like separate products, with
no shared navigation and no way back except the browser.

Requirements:

1. Every service is one click away from every other, with the same navigation on
   every page, so Scout reads as one product.
2. Links keep working: a bookmark, a shared link, or a new tab opened from inside a
   service lands on the same page, inside Scout.
3. Services keep their own origins, sessions and deployments. Nothing about a
   service's code depends on the launchpad beyond allowing it to frame the service.
4. Framing adds no way for content in one service to reach another service, or to
   send data out of Scout.
5. One mechanism on every site. Framing is not a per-site option.

## Decision

### Framed services next to a rail

The launchpad becomes a shell. Every page has a left rail built from the catalog
(ADR 0034). A chip with a subdomain destination opens framed at `/app/<chip id>`
beside the rail. A path destination is a launchpad page and renders with the rail.
A `url` destination opens on its own, following `newTab`.

- **Framing is always on.** There is no site setting to turn it off. Every service
  must let the launchpad frame it.
- **Frames are kept, up to a cap.** The three most recently used services stay
  loaded and hidden while another is shown, so switching back keeps their state. Older
  ones are unloaded.
- **The address bar follows the framed service:** `/app/<id>/<path>`. Reloading or
  sharing that address reopens the service at the same page.

### Deep links

A top-level navigation to a service URL goes through the launchpad:

- **The contract.** The edge redirects the browser to
  `https://<launchpad host>/open/<service host>/<path>?<query>`. It does this only
  for top-level document loads (`Sec-Fetch-Dest: document`) that did not come from
  the launchpad itself. Those are loads with `Sec-Fetch-Site` of `none` or
  `cross-site`, or `same-site` without a launchpad `Referer`.
  - Frame loads, API calls, downloads and sign-in callbacks are not redirected.
  - Clients that send no `Sec-Fetch-*` headers are not redirected either.
- **`/open`** accepts any subdomain of the launchpad's host. It frames the chip on
  that host whose path is the longest prefix of the URL. A host with no chip opens
  on its own, through a same-site script navigation that the edge lets through.
- **On-prem**, a Traefik `open-in-launchpad` route implements the contract for every
  subdomain.
- **Other edges** implement the same contract. Framed services' Ingresses carry the
  label `launchpad.scout.xnat.org/deep-link: "true"`, so a site's edge overlay can
  add its redirect to all of them with one rule. Because the redirect passes the
  request host through, the rule is the same for every service.

### Messages between the launchpad and services

Framed services may post two messages to the launchpad (`window.parent` or
`window.top`):

- **`scout:location`** reports the service's current address, so the address bar
  can follow it. The launchpad accepts it only from the frame's own chip origin, or
  one of that origin's subdomains for JupyterHub's per-user hosts. It keeps only the
  path.
- **`scout:open`** asks the launchpad to open another Scout page in the shell
  instead of a new tab. Report-viewer uses it for Send to XNAT. The sender's origin
  and the target must both be on the launchpad's host or a subdomain, and a chip
  must own the target. The launchpad answers `scout:opened`. Without that answer, the
  sender falls back to a new tab.

Supporting either message is optional. A service that sends neither still works
framed; its address bar just stays at the page it was opened on.

### Security

**Framing.**

- Every Scout service sends `frame-ancestors https://<launchpad host>`, so only the
  launchpad can frame it.
  - Services keep `X-Frame-Options: DENY` alongside it. Browsers ignore XFO when
    `frame-ancestors` is present, and scanners that only read XFO stay satisfied
    (ADR 0012).
  - XNAT keeps `SAMEORIGIN` plus the launchpad.
  - Report-viewer also admits chat, which embeds it.
- The launchpad sends `frame-ancestors 'none'`.

**The launchpad page.**

- **CSP with a fresh nonce per request:** `script-src 'self' 'nonce-…' 'strict-dynamic'`.
- **Frames:** `frame-src https://*.<launchpad host>`. A framed service can't navigate
  its frame off Scout.
- **Everything else:** `connect-src 'self'`, `object-src 'none'`, `base-uri 'self'`,
  `form-action 'self'`, and `upgrade-insecure-requests`.
- **Referrer-Policy:** Scout's shared header middlewares send
  `strict-origin-when-cross-origin`, so outside sites see only the origin, never a
  path.
  - Deep links rely on this. A stricter policy drops the same-site `Referer` that
    keeps `/open`'s fallback from looping.

**Sandboxed chips.**

- A chip can set `sandbox: true`. Its frame then gets `allow-scripts allow-same-origin
  allow-forms allow-modals allow-downloads`: no popups, and no navigation of the
  launchpad window.
- Chat sets it. Chat renders model output, and Open WebUI runs HTML embeds as the
  chat page. A script injected through report text or a tool result could otherwise
  wait for the user's next click, then open a popup or move the launchpad window to
  an outside URL carrying data. CSP has no rule for either.
- An invalid `sandbox` value rejects the chip rather than dropping the sandbox, the
  same as `audience`.

**Chat exfiltration channels.** Chat's own CSP (ADR 0009) and link filter (ADR
0010) cover most channels. The shell adds the rest:

| Channel | Control |
|---|---|
| Images, fonts, styles from outside hosts | chat CSP `img-src` / `default-src 'self'` |
| `fetch`, XHR, WebSockets to outside hosts | chat CSP `connect-src`: `'self'`, Keycloak, oauth2-proxy. No `ws:`/`wss:` schemes, which would match any host |
| Frames and form posts to outside hosts | chat CSP `frame-src`, `form-action` |
| Links in model output | link filter removes outside URLs |
| Chat navigating its own frame off Scout | launchpad `frame-src` |
| Popups and top-window navigation after a click | chip sandbox |
| WebRTC | not covered: CSP has no rule for it. Accepted risk; a browser policy on managed machines can disable it |

## Consequences

- Every service, including each Pluggable App, must send `frame-ancestors` admitting
  the launchpad. A service that refuses framing shows a blank frame. The chip
  authoring guide (`docs/source/customize/launchpad-chips.md`) states the
  requirement.
- Every edge must implement the deep-link contract. Without it, links into a service
  open the service without the rail, but still work.
- Services render inside a frame, so anything that breaks there is a bug to fix in
  that service, not a reason to open it on its own. Examples: an IdP login page that
  refuses framing, an `http://` redirect that becomes mixed content, or an app that
  opens popups.
  - Sign-in therefore happens at the top level: the launchpad sends the whole window
    to sign in when its session ends.
- ADR 0035 gives each service its own security headers on edges that can't add
  them. This ADR builds on that: the framing headers above are the service's own.
  The deep-link redirect is the one thing every edge must add. ADR 0035 itself is
  unchanged.
- Popups opened from a sandboxed chip are blocked, including links in chat answers
  meant to open in a new tab. Scout pages inside chat use `scout:open` instead.
