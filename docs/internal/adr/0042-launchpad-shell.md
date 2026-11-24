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
- **Frames are kept, up to a cap.** At most three services stay loaded, the one shown
  included; the others are hidden but keep running, so switching back keeps their
  state. Opening a fourth unloads the least recently used, which later reopens at the
  last page it reported; anything it hadn't saved is lost.
- **The address bar follows the framed service:** `/app/<id>/<path>`. Reloading or
  sharing that address reopens the service at the last page it reported.

### Deep links

A top-level navigation to a service URL goes through the launchpad:

- **The contract.** The edge redirects top-level `GET` document loads
  (`Sec-Fetch-Dest: document`) of a service URL from anywhere but the service's own
  origin (`Sec-Fetch-Site` of `none`, `cross-site` or `same-site`) to
  `https://<launchpad host>/open/<service host>/<path>?<query>`.
  - Everything else reaches the service: frame loads, form posts, same-origin
    navigation, fetch and XHR, and clients that send no `Sec-Fetch-*` headers. The
    identity hosts (Keycloak, oauth2-proxy) are never redirected.
  - A service page opened on its own by same-origin navigation (a new tab from inside
    the service) reopens inside the launchpad if it runs the shared address-bar
    script, which sends an unframed page to `/open`.
  - The edge can't tell what a page load is for. A sign-in callback that arrives at
    the top level, or a link that turns out to be a download, is redirected like any
    other and then loads in the app's frame.
- **`/open`** reads the service host from the raw request path and accepts it only
  when it is a subdomain of the launchpad's host, each label a DNS label. It frames
  the chip on that exact host, or failing that a chip whose host the URL's is a
  subdomain of (JupyterHub's per-user servers), choosing by longest path prefix and
  else the first. The chip reopens on its own host at the URL's path. A URL no chip
  owns goes to the launchpad's home page: Scout services open only inside the
  launchpad.
- **On-prem**, a Traefik `open-in-launchpad` route, deployed with the launchpad,
  implements the contract for single-label subdomains and JupyterHub's per-user
  hosts.
- **JupyterHub also redirects same-origin top-level loads**, on its own host and the
  per-user hosts. VS Code and the terminal open from JupyterLab in new tabs and can't
  run the shared script, so the edge sends them to `/open` instead. Frame loads are
  `Sec-Fetch-Dest: iframe`, so a page already in the launchpad is never redirected.
  A top-level JupyterHub sign-in callback carries its one-time code through `/open`
  into the frame, so the code passes through the launchpad's address.
- **Other edges** implement the same contract. Framed services' Ingresses carry the
  label `launchpad.scout.xnat.org/deep-link: "true"`, so a site's edge overlay can
  find them. Because the redirect passes the request host through, the rule is the
  same for every service.
  - **An ALB** takes the redirect per Ingress, as rules that are Ingress paths
    naming action annotations, in a rule without a host ahead of the service's own
    (an ALB rule holds five conditions, and the host would leave no room for the
    `GET` check). A rule without a host matches every host on its listener, so this
    suits only an ALB that serves that one service; a shared ALB needs the host
    condition back. A framed service's chart, Pluggable Apps included, must accept
    extra Ingress annotations and paths so the site can add the rules.

### Messages between the launchpad and services

Framed services may post three messages to the launchpad (`window.parent` or
`window.top`):

- **`scout:location`** reports the service's current address, so the address bar
  can follow it. The launchpad accepts it only from the service's own frame, sent
  from the chip's origin or one of its subdomains (JupyterHub's per-user hosts). It
  keeps the path, query and fragment, not the origin, so they reach the launchpad's
  address bar, history and logs: services must not report URLs with secrets in them
  (the shared script drops XNAT's CSRF token). The frame check rejects messages sent
  from content the service nests; it can't tell the service's own code from script
  that same-origin content runs in its page.
- **`scout:leaving`** names where a navigation in the service's frame is headed,
  for a destination page that can't report itself (XNAT's viewer). The launchpad
  checks it like `scout:location` and takes the address only if the frame then loads
  a new page within 30 seconds; a `scout:location` from that page replaces it. The
  shared script sends it for link navigations, not form posts or downloads.
- **`scout:open`** asks the launchpad to open another Scout page in the shell
  instead of a new tab. The launchpad accepts it from its own page or a service on a
  single-label subdomain of its host, but not from a sandboxed chip's origin (its
  pages may run content the service didn't write) or from JupyterHub's per-user hosts
  (users' own content). The target must be on the launchpad's host or a subdomain and
  owned by a chip. Neither the sending frame nor a user gesture is checked: the
  message lets a service move another service's frame to a page that service owns,
  not read it. Report-viewer, including inside chat, uses it for its actions. The
  launchpad answers `scout:opened`; without that answer the sender may try a new tab,
  which a sandbox can block.

Supporting any of them is optional. A service that sends none still works
framed; its address bar just stays at the page it was opened on.

XNAT loads the shared script from a plugin jar holding an XNAT header template. The
jar is the same for every site: instead of a configured launchpad address, it posts
to the host one label above XNAT's own, the single-label layout `scout:open` already
assumes. Each Scout release attaches it as `scout-frame-location-X.Y.Z.jar` for
deployments that install XNAT plugins by URL, and Ansible builds the same jar on the
control node, so air-gapped sites need no download.

### Security

**Framing.**

- Every service the launchpad frames admits it in `frame-ancestors`. On-prem,
  Scout's Traefik header middlewares supply this:
  - `frame-ancestors https://<launchpad host>`, with `X-Frame-Options: DENY` kept
    beside it. Browsers ignore XFO when `frame-ancestors` is present, and scanners
    that only read XFO stay satisfied (ADR 0012).
  - XNAT also admits itself, with `SAMEORIGIN`.
  - Report-viewer also admits chat, which embeds it.
  - Pages that aren't framed (sign-in, the popup-target middleware) keep their own
    policies.
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

**Sandboxed chips.**

- A chip can set `sandbox: true`. Its frame then gets `allow-scripts allow-same-origin
  allow-forms allow-modals allow-downloads`: no popups, and no navigation of the
  launchpad window.
- Chat sets it. Chat renders model output, and Open WebUI runs HTML embeds as the
  chat page. A script injected through report text or a tool result could otherwise
  wait for the user's next click, then open a popup or move the launchpad window to
  an outside URL carrying data. CSP has no rule for either.
- Chat's own CSP carries the same flags as a `sandbox` directive, so chat opened
  outside the launchpad can't open popups either. On-prem, Open WebUI's CSP
  middleware sets it; another edge sets it in Open WebUI's own CSP.
- An invalid `sandbox` value rejects the chip rather than dropping the sandbox, the
  same as `audience`.

**Chat exfiltration channels.** Chat's own CSP (ADR 0009) and link filter (ADR
0010) cover most channels, and the shell adds the rest. The table holds while chat
is framed, which the deep-link redirect makes the normal case. Script that model
output runs as chat's origin keeps that origin's access to chat itself; these
controls limit where it can send data.

| Channel | Control |
|---|---|
| Images, fonts, styles from outside hosts | chat CSP `img-src` / `default-src 'self'` |
| `fetch`, XHR, WebSockets to outside hosts | chat CSP `connect-src`: `'self'`, Keycloak, oauth2-proxy. No `ws:`/`wss:` schemes, which would match any host |
| Frames and form posts to outside hosts | chat CSP `frame-src`, `form-action` |
| Links in model output | link filter removes recognized outside `http(s)` and `www.` URLs; it is not a general HTML sanitizer |
| Chat navigating its own frame off Scout | launchpad `frame-src`. Not covered when chat runs on its own: CSP has no rule for a page navigating itself |
| Popups and top-window navigation after a click | chip sandbox; chat's CSP `sandbox` when chat runs on its own |
| WebRTC | not covered: CSP has no rule for it. Accepted risk; a browser policy on managed machines can disable it |

## Consequences

- Every service, including each Pluggable App, must send `frame-ancestors` admitting
  the launchpad. A service that refuses framing shows a blank frame. The chip
  authoring guide (`docs/source/customize/launchpad-chips.md`) states the
  requirement.
  - Where the edge can't set a service's headers (an ALB) and the service can't be
    configured to admit the launchpad, a site has to rewrite its headers, or its chip
    shows a blank frame there. The Temporal UI is one: it sends
    `X-Frame-Options: SAMEORIGIN` and no `frame-ancestors`.
- Every edge must implement the deep-link contract. Without it, links into a service
  open the service without the rail, but still work.
- Reopening at the same page has limits: a page that doesn't report its address
  (a form post's result, a page without the shared script) reopens at the last page
  that did, and a service's sign-in can replace the requested page with its home
  page (the pinned XNAT OpenID plugin does, before the user has an XNAT session).
- Services render inside a frame, so anything that breaks there is a bug to fix in
  that service, not a reason to open it on its own. Examples: an IdP login page that
  refuses framing, an `http://` redirect that becomes mixed content, or an app that
  opens popups.
  - Sign-in therefore happens at the top level. The launchpad notices that its own or
    the edge's session has ended (when an app frame loads, when the tab becomes
    visible, and each minute while it is visible) and sends the whole window to sign
    in. A service's own session ending inside its frame is for that service to
    handle.
- On edges without a header middleware (ADR 0035), the framing headers above are the
  service's own. The deep-link redirect is an exception to ADR 0035's shared edge:
  each site's edge adds it, since it depends on the edge's request matching. ADR 0035
  itself is unchanged.
- Popups opened from a sandboxed chip are blocked, including links in chat answers
  meant to open in a new tab. Report-viewer inside chat opens Scout pages through
  `scout:open`; chat itself can't.
