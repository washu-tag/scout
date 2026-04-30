# XNAT Dev Branch Review

A review of what was built and tested on the `xnat-dev-wt` branch, written
to answer five specific questions from John. Source material:
`xnat-deployment-plan.md`, `xnat-deployment-runbook.md`,
`xnat-k3d-runbook.md`, `xnat-k3d-progress.md`, plus the relevant Scout
roles (`keycloak`, `oauth2-proxy`, `jupyter`).

## 1. Is your understanding correct?

Mostly yes, with one important refinement about *where* the access gate
ended up living. Here is the same story retold in plain language for
someone who hasn't been deep in OIDC docs.

### What we set out to do

Stand up a fresh XNAT instance and have it authenticate users through
Scout's existing identity provider (Keycloak), so that:

- Anyone who already has a Scout account can sign in to XNAT without a
  separate username/password.
- An XNAT account is created automatically the first time someone signs
  in (no manual provisioning).
- Only people who are members of the Scout `scout-user` group get in;
  everyone else is denied.

Concretely: when a user clicks "Sign in with Scout" on the XNAT page,
XNAT hands the login off to Keycloak. If Keycloak is happy, it sends the
user back to XNAT carrying a signed token that names the user and lists
their groups. XNAT trusts that token and either creates or finds a
matching XNAT account.

### What we actually did

- **Tried dev03 first, fell back to k3d.** The intent was to deploy on
  the dev03 Scout cluster. Server problems on dev03 made that
  impractical, so we built a parallel local slice on `k3d` (a
  single-node Kubernetes that runs on the laptop) to exercise the same
  login flow end-to-end. The Ansible role / realm / values changes in
  this branch are written to apply equally to dev03 once it's back; the
  k3d slice adds extra wrapper scripts on top.
- **Brought up the Scout pieces XNAT depends on, and nothing else.** On
  k3d that's CloudNativePG (the operator that provisions Postgres
  databases) for Keycloak's database, then Keycloak itself with Scout's
  realm template imported, and a TLS cert good for `*.localtest.me`.
  The lake, analytics, monitoring, ingest pipelines, and
  oauth2-proxy were all left out — they aren't on the path the login
  flow exercises.
- **Modified Scout's Keycloak realm** in two ways that are durable
  (will land on dev03 the next time the realm is imported):
  - Added an `xnat` *client* — Keycloak's term for "an application
    allowed to ask Keycloak to authenticate users." That gives XNAT a
    `client_id` and `client_secret` so Keycloak can recognize XNAT's
    requests.
  - Added an `xnat-access` *client role* and mapped it onto the
    existing `scout-user` group, so every member of `scout-user` gets
    `xnat-access` automatically.
- **Modified Scout's Keycloak realm** in one more way that is currently
  k3d-only and still needs to be promoted to the role / realm template
  before dev03 sees it: a custom `browser-xnat-access` authentication
  flow, attached only to the `xnat` client, that denies the login if
  the user lacks the `xnat-access` role. This is built at runtime by
  `kcadm` calls inside `xnat/k3d/run-all.sh` step 5 — see "What this
  leaves for dev03" in `xnat-k3d-progress.md` for the two paths to
  promote it.
- **Deployed XNAT** from a local clone of NrgXnat's Helm chart at
  `/Users/jflavin/repos/helm-charts/helm/xnat`, with chart values that
  point its OpenID auth plugin at Scout's Keycloak. The chart provisions
  a separate CNPG cluster for XNAT's own database. An init container
  downloads the XNAT OpenID Auth Plugin JAR from Bitbucket and drops it
  into XNAT's plugins directory at startup.
- **Tested two users.** `alice` is in `scout-user`, `bob` is not. Both
  exist in Keycloak with passwords. `agent-browser` drives a real
  Chromium through the login flow for each user and asserts:
  - **alice** lands on the authenticated XNAT homepage.
  - **bob** is rejected and never reaches XNAT.

### One refinement to your description

You wrote: "the latter could not even load the XNAT front page (blocked
at Keycloak)." That's almost right but worth nuancing because it shaped
a real design decision in this branch:

Bob *can* load the XNAT front page anonymously (the home page is
public), and he *can* click "Sign in with Scout" and reach the Keycloak
login form. He can even type the right password and click Sign In. What
happens next is that Keycloak's custom `browser-xnat-access` flow
intercepts him after authentication and shows an access-denied page
without ever issuing the auth code that XNAT would need to create him a
user. So XNAT itself never sees bob.

The gate ended up on the **Keycloak side** rather than the XNAT side
because of a discovery midway through this work: the OpenID Auth Plugin
1.4.1-xpl does **not** read the `groups` claim out of the token, even
though we wired up a group-membership protocol mapper to put it there.
The plan-doc originally assumed `auto.enabled=false` (XNAT-side admin
approval) would do the gating; the progress-doc records the pivot to
"Plan B: gate inside Keycloak" once we read the plugin source and
confirmed there's no group-filter code path. We moved authorization
into Keycloak by attaching a conditional sub-flow to the `xnat`
client's browser flow that requires the `xnat-access` role.

That distinction — "blocked at the OIDC callback inside XNAT" vs.
"blocked by Keycloak before any token is issued" — matters because the
two failure modes look identical from the user's perspective but have
very different operational properties. The Keycloak-side gate is
strictly tighter: bob never gets a token, so there is no risk of XNAT
later treating a stale disabled user as enabled, and XNAT logs nothing
about the rejected attempt (good for audit cleanliness, less good if you
wanted XNAT to know about denied attempts).

## 2. Is XNAT behind oauth2-proxy?

**No, by deliberate design.** It is on oauth2-proxy's *bypass* list
(same posture as Keycloak's own admin URL, and as `auth.{hostname}`).

### A 30-second tour of OAuth, oauth2-proxy, and "behind"

- **OAuth/OIDC** is a protocol where one app ("the client") sends a
  user to a separate identity provider ("Keycloak" here), the user
  signs in there, and the identity provider sends them back to the app
  carrying a signed token that says "this user is X and is in groups
  Y and Z." The app trusts the signature, not the user's browser.
- **oauth2-proxy** is a small reverse-proxy that does an OIDC dance
  *for* an upstream application. The upstream app receives a request
  that has already been authenticated; it never sees the OIDC redirect.
  This is convenient for apps that don't speak OIDC themselves
  (Grafana, MinIO console, etc.) — oauth2-proxy enforces that the user
  is signed in and has been approved before any request reaches the
  app.
- **"Behind oauth2-proxy"** means: a request hits Traefik, Traefik
  routes it through oauth2-proxy first (via a `forwardAuth`
  middleware), and only if oauth2-proxy says "yes, signed-in approved
  user" does Traefik forward it to the app. If the user isn't signed
  in, oauth2-proxy redirects them to Keycloak, lands them back at
  oauth2-proxy with a session cookie, and *then* lets the request
  through. Most Scout services (Superset, Grafana, JupyterHub,
  launchpad) are wired up exactly that way.
- **"Bypassed"** means: requests skip the forwardAuth middleware and
  hit the app directly. Usually paired with the app speaking OIDC on
  its own. Scout already does this for Keycloak's admin UI itself
  (you can't put Keycloak behind a thing that depends on Keycloak)
  and `auth.{hostname}`.

### Why XNAT is bypassed, in plain terms

Three reasons, in increasing weight:

1. **Two OIDC dances would happen back-to-back.** XNAT itself runs an
   OIDC flow via the OpenID Auth Plugin. If oauth2-proxy were in front,
   the user would (a) get redirected to Keycloak by oauth2-proxy, (b)
   come back to oauth2-proxy with a session cookie, (c) get forwarded
   into XNAT, (d) get redirected to Keycloak *again* by XNAT's plugin,
   (e) come back to XNAT with an XNAT session. Two redirects, two
   cookie domains, two records of the login. It works, but it's
   confusing and easy to misconfigure.
2. **XNAT is not just a browser app.** Desktop uploaders, the DICOM
   ingest path, container-service callbacks, and any client using
   XNAT's own API tokens authenticate with mechanisms oauth2-proxy
   doesn't understand. Those clients can't pass an oauth2-proxy
   browser cookie because they don't have one. Putting XNAT behind
   oauth2-proxy would break all of them unless we cut a per-path or
   per-client-type bypass anyway.
3. **oauth2-proxy can't usefully *protect* XNAT either.** XNAT does
   its own auth — local DB users, API tokens, the OIDC plugin. The
   Scout-style "oauth2-proxy enforces approval, the app does its own
   authorization" model assumes the app trusts the proxy's identity
   header. XNAT doesn't have a hook to consume an external "you have
   been pre-approved" header, so oauth2-proxy in front would be doing
   a redundant gate, not an additional one.

The plan-doc puts this decision in writing in the "OAuth2 Proxy —
bypass it" section.

### What would have to change to put XNAT behind oauth2-proxy?

You'd want one of:

- **Drop the OpenID Auth Plugin and rely on a header.** Some XNAT
  deployments use a header-trust auth module (Apache `mod_auth_*`
  patterns) that says "trust whatever is in the `X-Remote-User`
  header." oauth2-proxy can populate `X-Auth-Request-User` /
  `X-Auth-Request-Email`. There's no off-the-shelf XNAT plugin that
  consumes those headers in the 1.x series; you'd have to write or
  port one (e.g. an XNAT auth provider that maps an upstream-trusted
  header to an XNAT user). That's a meaningful bit of XNAT-side work,
  not a config change.
- **Keep the OpenID Auth Plugin and accept the double-OIDC dance.**
  Workable but ugly; you'd also have to carve out non-browser paths
  (REST API, DICOM) from the oauth2-proxy middleware, which makes the
  config noisy and the security story muddled.
- **Skip OIDC entirely and let oauth2-proxy be the only auth layer.**
  XNAT would still need *some* user identity for project ACLs and
  audit; oauth2-proxy can pass that as a header, but you still need
  the XNAT-side header-trust module from option 1.

For Scout the bypass-with-XNAT's-own-plugin posture is the lower-cost
path and has been adopted; revisiting it would only make sense if a
specific feature (single sign-out, unified session timeout, etc.) made
the XNAT-side OIDC plugin a blocker.

## 3. Auto-redirect on login, or click-through?

**Click-through.** The user lands on the XNAT homepage anonymously and
has to click "Sign in with Scout" to start the OIDC flow. The "Sign in
with Scout" link is rendered from
`openid.keycloak.link=<a href="/openid-login?providerId=keycloak">…</a>`
in the openid-provider properties.

Whether to auto-redirect is configurable on the XNAT side, not the
Keycloak side, and it's a small piece of XNAT site config rather than a
plugin change:

- XNAT's homepage layout is a Velocity template; making `/` redirect
  unconditionally to `/openid-login?providerId=keycloak` for
  unauthenticated users is doable but breaks the local-admin login
  path (you'd need a query-string escape hatch like
  `/?login=local`).
- A more contained option: keep the homepage public, but make the
  *login* page (`/app/template/Login.vm`) auto-submit to the OIDC
  provider when only one external provider is configured and the local
  DB is hidden. This is closer to how Superset and Grafana behave
  inside Scout (the local-DB login form is hidden and the Keycloak
  button is the only path). XNAT supports disabling localdb at the
  site-config level (`enabledProviders=["keycloak"]`), which is what
  forces this behavior; the runbook currently sets
  `enabledProviders=["localdb", "keycloak"]` to keep the rescue path
  open during dev.

Neither change is wired up on this branch; both are decisions for the
"productionize this for dev03 / on-prem" pass. For the test on this
branch the click-through is intentional — it's part of how the
agent-browser script asserts the flow (`find text "Sign in with
Scout" click`).

## 4. Seamless logout from XNAT when logging out of Scout?

**Possible, but not wired up here.** Three layers to think about,
roughly in order of how cleanly they'd integrate.

### What "logout" means in each layer

1. **The XNAT session cookie** in the user's browser. Killed by
   visiting `/app/action/LogoutUser` or by waiting out the session
   timeout. XNAT's own logout link does the former.
2. **The Keycloak SSO session.** Killed by visiting Keycloak's
   `end_session_endpoint`, which Scout already uses today: every
   service's "Sign out" button redirects through
   `oauth2-proxy /oauth2/sign_out`, which clears its own cookie
   *and* hits Keycloak's end-session URL with `id_token_hint=...`.
   That's how Superset, Grafana, JupyterHub, and Open WebUI all sign
   the user out of Keycloak when the user signs out of any one of
   them today (see `oauth2_proxy_signout_url` references in the
   roles).
3. **Other services' local sessions.** If Grafana has an active
   cookie, killing the Keycloak SSO session does *not* immediately
   kill Grafana's cookie — Grafana only re-checks Keycloak when the
   cookie expires or the user navigates somewhere that triggers
   re-auth.

The "seamless logout" question is really about layer 3 — propagating
"the user signed out of Scout" into XNAT's local session.

### The mechanisms available

There are two standard OIDC features for this:

- **RP-Initiated Logout (front-channel).** The user-driven flow.
  XNAT's logout link can `302` to Keycloak's `end_session_endpoint`,
  Keycloak signs them out and shows a redirect or a button back to
  the original site. This is what oauth2-proxy already does for the
  rest of Scout. Wiring XNAT into this would mean replacing the
  default XNAT logout target with `oauth2_proxy_signout_url` (or
  Keycloak's end-session endpoint directly), so that signing out of
  XNAT also signs the user out of Scout. That's the easy direction:
  XNAT → Scout.
- **Back-Channel Logout.** The reverse direction: when Keycloak ends
  a session (because the user signed out somewhere else), Keycloak
  POSTs a signed logout token to a `backchannel_logout_uri` registered
  on each client, telling the client "kill the session you have for
  this user." XNAT 1.x and the OpenID Auth Plugin 1.4.1-xpl do **not**
  expose a back-channel logout endpoint — there's no code in the
  plugin source that handles `logout_token`. Without that, signing
  out of (say) launchpad will end the Keycloak SSO session but the
  user's XNAT cookie keeps working until it expires.

### What it would take to make logout seamless from Scout → XNAT

In rough order of effort:

- **Front-channel only, easy version:** ensure all Scout exit points
  (the launchpad sign-out button, every service's sign-out menu)
  point at oauth2-proxy's sign-out URL, and add an XNAT plugin patch
  or a tiny custom XNAT theme that *also* surfaces a "Sign out of
  Scout" link that does the same thing. Doesn't end the XNAT cookie
  on its own, but means the next time the user does anything
  Keycloak-mediated, they'll be re-auth'd. Cheap.
- **Front-channel, full version:** override XNAT's
  `LogoutController` to also redirect to oauth2-proxy's sign-out URL
  after killing the local session. This makes XNAT → Scout sign-out
  symmetric with the rest of the platform. Small Velocity / Spring
  override; no plugin changes needed.
- **Back-channel (truly seamless from anywhere):** add a
  `backchannel_logout_uri` to the `xnat` Keycloak client and
  implement a corresponding endpoint inside XNAT that validates the
  `logout_token` JWT and invalidates the matching XNAT session. This
  is the only path that gives "user signs out of launchpad → XNAT
  cookie is dead within seconds" without the user touching XNAT.
  Effort: a small XNAT plugin, or a fork of openid-auth-plugin that
  adds the endpoint. Worth doing once Scout has more than one place
  the user can be active simultaneously.

None of the above is built on this branch. The question is recorded
as a future-work item.

## 5. Programmatic XNAT access from Jupyter using existing tokens?

This is the most interesting question because it bumps into a real
limitation of how Scout currently moves OAuth tokens into the
notebook environment. The short answer: **possible in principle, with
modest config additions; not possible today out of the box.** Detail
below.

### What "tokens already in the environment" actually means in Scout's Jupyter

Scout's JupyterHub uses `oauthenticator.generic.GenericOAuthenticator`
to do the OIDC dance against Keycloak when a user signs in to
JupyterHub. Two settings in the role
(`ansible/roles/jupyter/templates/values.yaml.j2:67-68`) are relevant:

```yaml
GenericOAuthenticator:
  ...
  enable_auth_state: true
  auth_state_groups_key: 'oauth_user.groups'
```

`enable_auth_state: true` tells JupyterHub to keep the OAuth response
(access token, refresh token, ID token, userinfo) **server-side**,
encrypted with `JUPYTERHUB_CRYPT_KEY`, attached to the user record.
By default, this auth state is **not** propagated into the user's
notebook pod. To get it into the notebook, you need a `pre_spawn_hook`
or `auth_state_hook` that copies fields out of the auth state into
environment variables on the spawned pod. Scout's role does not
configure either today — the spawner's `extraEnv` block populates
Trino connection info and CA bundle paths, but no auth-related env
vars.

Separately, the JupyterHub *server* exposes an `X-Auth-Request-Access-Token`
header from oauth2-proxy (because JupyterHub's ingress is behind
oauth2-proxy — see `ansible/roles/jupyter/templates/values.yaml.j2:90-91`
and `oauth2-proxy/tasks/deploy.yaml:93`). But that header reaches the
JupyterHub *Hub* container, not the user's notebook server, and it
isn't automatically forwarded to the user environment.

So as of this branch, a notebook user does not have a Keycloak access
token in any environment variable, file, or in-process API. Asking
Python `requests` to call XNAT with "the existing auth tokens" would
not find any.

### What would need to be different

There are two reasonable paths, both of which are config-only on the
Scout side; neither requires changes to XNAT.

#### Path A: forward the Keycloak access token via a JupyterHub auth_state_hook

Add a `c.Spawner.auth_state_hook` (in `hub.extraConfig` of the
JupyterHub Helm values) that copies the access token (and optionally
the refresh token) from `auth_state` into env vars on the spawned
notebook pod:

```python
async def auth_state_hook(spawner, auth_state):
    if not auth_state:
        return
    spawner.environment['KEYCLOAK_ACCESS_TOKEN'] = auth_state['access_token']
    spawner.environment['KEYCLOAK_REFRESH_TOKEN'] = auth_state.get('refresh_token', '')

c.Spawner.auth_state_hook = auth_state_hook
```

(Reference: zero-to-jupyterhub-k8s docs on auth state and pre-spawn
hooks; oauthenticator's `auth_state` schema.)

Then, inside a notebook, calling XNAT becomes:

```python
import os, requests
xnat_token_resp = requests.post(
    "https://keycloak.scout.example/realms/scout/protocol/openid-connect/token",
    data={
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": os.environ["KEYCLOAK_ACCESS_TOKEN"],
        "audience": "xnat",
        "client_id": "jupyterhub",
    },
)
xnat_access = xnat_token_resp.json()["access_token"]

# Or: send the original access token directly to XNAT, if the xnat
# client is configured to accept tokens issued for the jupyterhub
# client (i.e. shared audience).
r = requests.get(
    "https://xnat.scout.example/data/projects",
    headers={"Authorization": f"Bearer {xnat_access}"},
)
```

The trick that's *not* in the snippet above: XNAT 1.9.x does not
natively accept Keycloak access tokens as XNAT API auth. The OpenID
Auth Plugin handles the *interactive* OIDC code flow, but the XNAT
REST API authenticates with XNAT-issued sessions or XNAT-issued
"alias tokens." So even with the access token in `os.environ`, you
can't just `Authorization: Bearer <keycloak token>` against XNAT and
expect it to work. You'd need one of:

1. **A bearer-token API-auth shim in XNAT.** Either a small XNAT
   plugin that validates incoming Keycloak JWTs (signature, audience,
   expiry, `xnat-access` role) and maps them to an XNAT user, or a
   patched openid-auth-plugin that adds an API filter alongside its
   browser-OIDC filter. This is real XNAT work, not config.
2. **Trade the Keycloak token for an XNAT alias token, in advance.**
   The user opens XNAT in a browser once (which goes through the
   normal OIDC flow), generates an alias token via
   `POST /data/services/tokens/issue`, copies it into a Jupyter env
   var (or pastes it into a notebook). Subsequent notebook calls use
   `Authorization: Basic <username:alias-secret>`. Works today,
   doesn't require any XNAT-side code, but isn't "no clicks" — it's
   "one click per token rotation."
3. **Keycloak token exchange to mint XNAT-issued credentials.**
   Configure the xnat Keycloak client to accept token-exchange from
   the jupyterhub client (`token_exchange` in the Keycloak client
   permissions), and have an XNAT-side service account that knows
   how to mint an XNAT alias token for the user identified in the
   exchanged token. Still needs an XNAT-side endpoint, so back to
   (1) in spirit.

#### Path B: go through oauth2-proxy

Since JupyterHub's ingress already passes
`X-Auth-Request-Access-Token` through oauth2-proxy, you could in
theory have the notebook server itself forward this header into a
client-callable endpoint. In practice the Hub-vs-singleuser split
means the header lands on the hub pod, not the user notebook pod, so
it's still a hook-and-env-var problem. Path A is cleaner.

### What you can confirm from existing docs / source without running it

- **oauthenticator's `auth_state` is the documented seam** for moving
  OIDC tokens into spawned notebooks. The shape of `auth_state` for
  GenericOAuthenticator is `{"access_token": ..., "refresh_token":
  ..., "scope": ..., "oauth_user": {...userinfo...}}`. This is
  observable from the oauthenticator source under
  `oauthenticator/generic.py` and is referenced in the
  zero-to-jupyterhub deployment guide.
- **JupyterHub's `pre_spawn_hook` / `auth_state_hook`** are
  documented Spawner hooks; both can mutate `spawner.environment`.
- **Keycloak supports token exchange** (RFC 8693) but it's
  off-by-default and per-client-pair scoped.
- **The oauth2-proxy `X-Auth-Request-Access-Token` header** is
  already being forwarded for Scout's Jupyter ingress — see the
  Traefik middleware definition; `authResponseHeaders` includes
  `X-Auth-Request-Access-Token`. That confirms the token *is* in
  flight; it's just not landing in the notebook user's process.
- **The XNAT OpenID Auth Plugin source** does not implement an API
  bearer-token filter (only the browser interactive flow). This is
  confirmable by grepping the cloned plugin at
  `/Users/jflavin/repos/openid-auth-plugin` for `BearerToken`,
  `Authorization` header parsing in non-OAuth flows, etc.

### Net answer to "would this work without explicit username/password?"

> Assuming Scout's Ansible is also modified to allow notebook pods
> network egress to Keycloak (`https://keycloak.{hostname}`) and to
> XNAT (`https://xnat.{hostname}` or its in-cluster Service). On dev03
> these may need a NetworkPolicy carve-out or DNS entry depending on
> how the namespace is locked down today; the question below assumes
> that's done. The egress isn't the blocker either way — the blockers
> below are about *what's in the notebook environment* and *what XNAT
> accepts at its API*, not about whether the packets can flow.

In the current state of this branch: **no, not without extra setup.**
The pieces that are missing:

1. The JupyterHub auth_state_hook that copies the Keycloak access
   token into the notebook pod's environment. *(One-line config
   change to Scout's jupyter role, if we want it.)*
2. Some XNAT-side mechanism for accepting that token as API auth.
   *(Either a thin XNAT plugin, or a Keycloak-token-exchange-then-
   alias-token bridge, or a token-exchange + a small custom XNAT
   endpoint. Not a one-line change.)*

The closest you can get with no XNAT-side work is the **alias-token
trade** in option (2) above: open XNAT once in a browser (via the
normal click-through OIDC flow), mint an alias token, paste into a
notebook env var, use it for the rest of that token's lifetime.
That's what Scout users would realistically use today, and it'd be
worth wiring into the post-deploy docs as the recommended pattern
even after we put auth_state in place — XNAT alias tokens are the
canonical mechanism for non-browser XNAT clients.

## What this branch is and isn't ready for

- **Ready to land on dev03** (once dev03 is back): the realm template
  changes (`xnat` client, `xnat-access` role, group mapping) and the
  Helm-values workarounds for the upstream chart bugs. These are not
  k3d-specific.
- **Needs promotion before dev03 sees end-to-end gating:** the
  `browser-xnat-access` flow, currently built imperatively by `kcadm`
  in `xnat/k3d/run-all.sh` step 5, needs to either move into an
  Ansible task under the `keycloak` role or be expressed in
  `scout-realm.json.j2` so keycloak-config-cli imports it. Decision
  point flagged in `xnat-k3d-progress.md`.
- **Not built on this branch:** seamless logout (front-channel
  override or back-channel endpoint), Jupyter-to-XNAT token
  forwarding, oauth2-proxy integration, DICOM ingest path,
  air-gapped plugin delivery, multi-replica HA. Each is recorded as
  a deferred item in the relevant doc.
