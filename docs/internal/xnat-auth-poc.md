# XNAT + Scout Auth — POC Findings and Next-Phase Proposal

**Status:** synthesis / research. Not a decision doc, not an ADR. Written
to give the team the learnings from the first POC and a proposal for the
shape of the next one to react to before we invest in implementation
detail.

**Audience:** Scout engineers familiar with Scout's auth posture
(Keycloak, oauth2-proxy, the `scout-user` group gate) but not
necessarily with XNAT internals.

## 1. What we set out to do

Stand up a fresh XNAT instance integrated with Scout's existing
Keycloak so that:

- A user already authenticated to Scout can sign in to XNAT without a
  separate username/password.
- An XNAT account is created automatically the first time a Scout user
  signs in (no manual provisioning step).
- Only members of the Scout `scout-user` group are allowed in;
  everyone else is rejected.
- Eventually: a Jupyter notebook user can call XNAT's REST API
  programmatically using credentials they already have, without
  pasting tokens or passwords.

The first POC focused on the first three goals end-to-end. The fourth
was deferred but informs the architecture proposal in §6.

## 2. What we built

### 2.1 Cluster slice

The original target was the `dev03` Scout cluster. Server problems on
dev03 made that impractical, so we built a parallel slice on `k3d` (a
single-node Kubernetes that runs on the laptop) to exercise the same
login flow end-to-end. Everything described below works on either; the
realm/role/values changes are not k3d-specific.

What's deployed in the slice:

- CloudNativePG (CNPG) operator and a Postgres cluster for Keycloak.
- Keycloak with Scout's realm template imported via
  `keycloak-config-cli`.
- A second CNPG cluster, provisioned by the XNAT Helm chart, for
  XNAT's own database.
- XNAT itself (NrgXnat Helm chart from a local clone), with the
  XNAT OpenID Auth Plugin (`openid-auth-plugin-1.4.2-SNAPSHOT`,
  distributed as `1.4.1-xpl`) downloaded at startup by an init
  container and the JVM truststore extended to trust mkcert's root CA.
- A `prefs-init.ini` mounted into XNAT's home dir to skip the
  first-boot setup wizard.

What we deliberately omitted: the lake, analytics, monitoring, ingest
pipelines, oauth2-proxy, multi-replica HA, air-gapped delivery, DICOM
ingest. None of those are on the path the login flow exercises.

### 2.2 Keycloak realm changes

These are the durable, non-k3d-specific changes:

- An `xnat` Keycloak client (OIDC, code flow with PKCE) added to
  `scout-realm.json.j2`. The `client_id` default lives in
  `roles/scout_common/defaults/main.yaml`; the secret is in the
  per-deployment inventory.
- An `xnat-access` *client role* on the `xnat` client, mapped from the
  existing `scout-user` group. Members of `scout-user` get
  `xnat-access` automatically.
- Two cosmetic client roles (`xnat-user`, `xnat-admin`) mapped from
  `scout-user` / `scout-admin`. These don't gate anything today —
  XNAT 1.x doesn't consume IdP roles for authorization — but match
  the realm shape every other Scout service uses.
- A Group Membership protocol mapper on the `xnat` client that emits
  the user's groups as a `groups` claim in ID/access tokens and
  userinfo. (Built in the k3d test by `kcadm`. Not yet in the realm
  template.)

One additional Keycloak change is currently k3d-only and needs to be
promoted before any production deployment sees end-to-end gating: a
`browser-xnat-access` authentication flow, attached only to the `xnat`
client, that denies the login if the user lacks the `xnat-access`
role. It's built imperatively via `kcadm` calls in the k3d driver
script. Promoting it means either an Ansible task under the `keycloak`
role or expressing the flow override directly in the realm template
for `keycloak-config-cli` to import. Both are feasible; the realm
template route is cleaner if `keycloak-config-cli`'s flow-import
support handles conditional executions reliably.

### 2.3 XNAT OpenID Auth Plugin configuration

The plugin reads `/data/xnat/home/config/auth/<provider>-provider.properties`
at startup. Settings used in the POC:

- `auth.method=openid`, `type=openid`, `provider.id=keycloak`.
- `siteUrl=https://xnat.{hostname}` and `preEstablishedRedirUri=/openid-login`
  — the plugin builds OIDC redirect URIs by concatenating these.
- `openid.keycloak.{accessTokenUri,userAuthUri,userInfoUri}` pointing
  at Keycloak's standard endpoints under the `scout` realm.
- `openid.keycloak.clientId=xnat`, `clientSecret=<from realm template>`.
- `auto.enabled=true`, `forceUserCreate=true`, `auto.verified=true` —
  auto-provision and auto-enable Keycloak users on first login.
- `usernamePattern=[providerId]-[sub]` — XNAT username is
  `keycloak-<sub>`.
- `pkceEnabled=true`.
- `openid.keycloak.link=<a href="/openid-login?providerId=keycloak">Sign in with Scout</a>` —
  rendered on XNAT's login page.

XNAT site config also has its own list of enabled providers
(`["localdb", "keycloak"]`), set via `POST /xapi/siteConfig`. The
plugin's runtime check uses a different list it builds from the
properties files, so the site-config flag is necessary but not
sufficient to make a provider work.

### 2.4 Test harness

Two users in Keycloak: `alice` (in `scout-user`) and `bob` (not).
`agent-browser` drives a real Chromium through the OIDC flow for each
and asserts:

- alice → completes Keycloak login → lands on the XNAT homepage as a
  provisioned user.
- bob → reaches the Keycloak login form, enters credentials, is
  rejected by Keycloak's `browser-xnat-access` flow before any auth
  code is issued. XNAT never sees bob.

Both assertions pass.

## 3. What worked well

- **The realm-template + role-mapping pattern is sound.** Adding the
  `xnat` client and the `xnat-access` role in `scout-realm.json.j2`
  followed Scout's existing per-service shape. Anyone reading the
  realm template tomorrow finds XNAT in the same place every other
  service lives.
- **Auto-provisioning on first login works as advertised.** With
  `auto.enabled=true` and `forceUserCreate=true`, alice's first OIDC
  round-trip both creates and enables her XNAT account. No admin
  click-through.
- **The `prefs-init.ini` route bypasses the first-boot setup wizard
  entirely.** XNAT comes up ready to use, no manual site-URL entry
  or admin-password change prompt. This is XNAT's own
  `/data/xnat/home/config/prefs-init.ini` mechanism, so it stays
  upstream-compatible.
- **`agent-browser`-driven assertions catch real failures.** During
  development at least three different breakages (DNS, plugin
  config, missing protocol mapper) showed up as test failures rather
  than as silent "looks logged in but isn't."

## 4. What didn't work / had to be worked around

### 4.1 The OpenID Auth Plugin has no group-claim filter

The plan-doc originally assumed `openid.keycloak.allowedGroups` would
filter on the `groups` claim. Reading the plugin source confirms it
doesn't exist: there's no `allowedGroups` / `allowed_groups` /
`allowed.groups` reference anywhere in `src/main`, and the only
claim-based filter the plugin reads is the email-domain whitelist
(`shouldFilterEmailDomains` / `allowedEmailDomains`). The `groups`
claim makes it into the JWT but no plugin code path consumes it.

The plan-doc's original mitigation (`auto.enabled=false`,
admin-approval gate) is functional but moves the gate from Scout's
authorization model to XNAT's, which means a Scout admin who is also
an XNAT admin has to approve every new user inside XNAT. That's
worse than the `scout-user` gate every other Scout service uses.

We pivoted to gating in Keycloak instead — the `xnat-access` role
plus the `browser-xnat-access` conditional flow described in §2.2.

### 4.2 The plugin's bundled logback config drops most debug logging

The plugin ships a logback config that pins the
`au.edu.qcif.xnat.auth.openid` package logger at INFO with
`additivity="false"`. The actual callback logic — `attemptAuthentication`
in `OpenIdConnectFilter` — logs at DEBUG, so on a failure you see
nothing in `openid.log`. The first time we hit a callback failure we
spent time debugging "user creation isn't running" before realizing
it was actually "the pod can't reach Keycloak."

This bites any future debugging. The two ways out are patching the
plugin's logback config or providing an override at deploy time.
Neither is currently wired up.

### 4.3 In-pod DNS for `keycloak.localtest.me` resolved to 127.0.0.1

`*.localtest.me` resolves to `127.0.0.1` via public DNS. From the
host that's the k3d load balancer; from inside a pod that's the
pod's own loopback. The XNAT pod's HTTPS calls to
`keycloak.localtest.me/.../token` got "Connection refused" before
any plugin logic ran. Combined with §4.2, the failure looked like a
plugin bug instead of a DNS issue.

Fix: a CoreDNS `*.server` override that points
`keycloak.localtest.me` at Traefik's cluster IP. Scout's `k3s` role
exposes `coredns_extra_server_blocks` for exactly this kind of
override; the k3d slice writes the same `coredns-custom` ConfigMap
shape directly because k3d isn't `k3s.yaml`-managed.

### 4.4 Three upstream chart bugs

NrgXnat's Helm chart (1.0.1 / appVersion 1.9.2.1) has issues we
papered over rather than patching. Worth recording so they're
findable:

- **`templates/_postgresql.tpl` whitespace bug.** When
  `cnpg.external.postgresqlPort` is empty the template emits
  `\n"5432"\n` at column 0 inside the rendered block scalar in
  `statefulset.yaml`, which breaks the YAML. Workaround: set
  `cnpg.external.postgresqlPort: 5432` in values.
- **Mail subchart unconditional + hardcoded secret name.** The
  `bokysan/postfix` subchart has no `mail.enabled` flag and
  references a `postfix-password` Secret that doesn't exist by
  default. Without the secret, `xnat-mail-0` sits in
  `CreateContainerConfigError` and `helm install --wait` never
  returns even though XNAT itself is healthy. Workaround: pre-create
  a placeholder secret.
- **Default startup probe too tight for first-boot Hibernate DDL.**
  Chart sets `failureThreshold=15 * periodSeconds=10 = 150s`. First
  boot takes 3–5 minutes natively. Workaround: bump to 60 (10 min)
  and the helm `--wait` to 20m.

### 4.5 Apple Silicon — `xnatworks/xnat-web` is x86_64 only

The image is single-arch (`linux/amd64`). On arm64 hosts, k3d /
containerd transparently runs it through `qemu-x86_64` user-mode
emulation. First boot under emulation runs roughly an order of
magnitude slower than native — we observed 40+ minutes of CPU-bound
silence past the access-log line that says XNAT is starting, with no
indication it would terminate. The k3d test passes on x86_64 hosts.
On arm64 the realistic options are an x86_64 VM, validating on a
real x86_64 cluster, or accepting hour-plus runtimes. Not something
we should paper over from the deploy side; needs an upstream
multi-arch image.

### 4.6 Click-through login, public homepage, no programmatic API path

These three are not bugs but consequences of the OIDC-plugin
architecture that we'll come back to in §5. Listed here so the "what
didn't work well" inventory is complete:

- The XNAT homepage is browseable anonymously (no auth gate at the
  ingress; the `requireLogin=true` site setting forces a login form
  but doesn't change the network reachability).
- Login is a click-through, not an auto-redirect. The user lands on
  the homepage and clicks "Sign in with Scout."
- A notebook user has no path to call XNAT's REST API with their
  existing Scout credentials. The plugin handles the interactive
  browser code flow only — there is no inbound bearer-token filter
  on REST API paths in the plugin source.

## 5. What is provisional because we used the OIDC plugin

These choices were specifically downstream of "use the XNAT OpenID
Auth Plugin, do OIDC inside XNAT." If we replace the plugin, every
one of them is reopen-able:

- **XNAT is bypassed by oauth2-proxy.** The plugin runs an
  interactive OIDC flow inside XNAT itself; oauth2-proxy in front
  would mean two consecutive redirects to Keycloak with two cookie
  domains and (potentially) two distinct user identities for one
  Keycloak login. So we left XNAT off Scout's standard auth
  middleware.
- **The XNAT ingress is publicly reachable.** The OIDC callback path
  (`/openid-login`) is mid-auth — the user has no session yet — so
  it can't itself require auth. With the plugin doing OIDC inside
  XNAT, that callback has to be reachable, which means the ingress
  is public. Site-config flags can force a login form on the
  homepage, but the network endpoint is still open.
- **Login is a click-through.** The plugin renders a "Sign in with
  Scout" link from `openid.keycloak.link`. Auto-redirect would mean
  XNAT-side template overrides and removing localdb from
  `enabledProviders`, both possible but neither built.
- **Authorization gate moved to Keycloak.** Because the plugin
  doesn't read the `groups` claim, we can't do an XNAT-side
  group-membership check. The `browser-xnat-access` flow is the
  workaround. (This is the "Plan B" in the progress notes.) It works,
  but it has a quirk: XNAT logs nothing about denied attempts because
  Keycloak never issues a code, so audit-from-XNAT is incomplete.
- **No programmatic API access from notebooks.** The plugin handles
  only the browser code flow. There's no inbound `Authorization:
  Bearer` filter on REST paths. A notebook can't call XNAT with its
  existing Keycloak credentials.
- **No seamless single sign-out.** The plugin doesn't expose an OIDC
  back-channel logout endpoint and doesn't parse `logout_token`
  JWTs. Signing out of (say) launchpad ends the Keycloak SSO
  session but leaves XNAT's local session cookie alive until it
  expires.
- **The bundled Bitnami `redis` and `bokysan/postfix` subcharts** are
  semi-related. The chart deploys them but doesn't wire them into
  XNAT's config. We disabled redis (`redis.enabled: false`); postfix
  we left running because the chart can't disable it cleanly. None
  of this is plugin-driven, but the next-phase deployment can decide
  whether to keep working around the chart's defaults or move to a
  fork / different chart.

The key observation: most of the "different from how Scout does
auth" things are downstream of one decision (use the plugin), not
downstream of XNAT itself. If we replace the plugin, the rest can
follow Scout's existing pattern.

## 6. Proposal: oauth2-proxy in front + a small XNAT plugin

The next POC should move auth into the same shape every other Scout
service uses: oauth2-proxy fronts the application; the application
trusts identity headers. XNAT gets a small custom plugin that
provides two Spring Security filters — one for browser traffic
arriving via oauth2-proxy, one for programmatic API traffic carrying
a Keycloak-issued JWT — sharing a common user-mapping and
provisioning service.

We are not attempting a transition / coexistence with the existing
plugin. The next POC is a fresh XNAT.

### 6.1 Architecture sketch

Two ingress paths to the same XNAT:

```
Browser:    User → Traefik → oauth2-proxy → XNAT
                                   (sets X-Auth-Request-* headers)

API/script: Notebook → Traefik → XNAT  (carrying Authorization: Bearer <jwt>)
```

Browser path: oauth2-proxy handles the OIDC dance, validates the
session cookie on every request, sets identity headers
(`X-Auth-Request-User`, `X-Auth-Request-Email`, `X-Auth-Request-Groups`)
and forwards. The XNAT plugin's header-trust filter reads those
headers, looks up or auto-creates the matching XNAT user, and
attaches a Spring Security principal. This is the shape Grafana,
Superset, JupyterHub, Open WebUI, and launchpad already use.

API path: bypasses oauth2-proxy. The XNAT plugin's bearer-token
filter validates the incoming `Authorization: Bearer <jwt>` directly
against Keycloak's JWKS (issuer, signature, expiry), checks the
`xnat-access` role claim, and maps to an XNAT user. This avoids
oauth2-proxy's strict audience-matching behavior (see §6.4) and
keeps the JWT-validation logic inside the same plugin that does the
header-trust path, sharing the user-mapping code.

Both paths funnel into a shared `UserProvisioningService` inside the
plugin: same Keycloak-`sub`-to-XNAT-user mapping, same
auto-provisioning logic, same `xnat-access` role check, same
username pattern. No duplicated code.

### 6.2 What this gets us

Going through the §5 list:

- oauth2-proxy fronts XNAT browser traffic — same posture as every
  other Scout service.
- XNAT's public ingress goes away for the browser path. (DICOM
  remains a separate concern; see §7.)
- Auto-redirect: oauth2-proxy is the auth gate, so unauthenticated
  browsers get sent to Keycloak immediately rather than landing on
  an anonymous homepage.
- `scout-user` gating returns to Scout's standard pattern. The role
  check happens inside the plugin from the JWT's role claim, not
  via a Keycloak custom flow. The `browser-xnat-access` flow
  override can be removed; the realm template stays simpler.
- Programmatic API access from notebooks works. A notebook with the
  user's Keycloak access token can call XNAT directly. (Subject to
  the audience/exchange decision in §6.4.)
- XNAT logs denied attempts (the bearer filter sees them), so audit
  from XNAT is complete.

It also makes seamless logout cleaner if we ever want to do it
(out of scope for this POC, recorded in §7).

### 6.3 The XNAT plugin

Concrete shape, based on what we know about XNAT 1.x's Spring
Security extension points:

- A new `BaseXnatSecurityExtension` subclass — the same seam the
  current OpenID plugin uses. The pattern is to override
  `configure(HttpSecurity http)` and call `http.addFilterBefore(...)`
  / `addFilterAfter(...)` with our filters. XNAT discovers all
  `BaseXnatSecurityExtension` beans automatically.
- **Filter A — `HeaderTrustFilter`.** Active on browser paths.
  Reads `X-Auth-Request-User`, `X-Auth-Request-Email`, and
  `X-Auth-Request-Groups` (or whatever oauth2-proxy is configured
  to forward). Refuses the request if the headers are absent (i.e.
  request didn't come through oauth2-proxy). Calls the shared
  provisioning service and attaches the principal.
- **Filter B — `BearerTokenFilter`.** Active on API paths. Reads
  `Authorization: Bearer <jwt>`. Validates via Keycloak's JWKS
  (cached). Checks the `xnat-access` role claim. Calls the shared
  provisioning service.
- **`UserProvisioningService`.** Pure logic. Given a normalized
  identity (`{sub, username, email, given_name, family_name,
  groups, roles}`), returns the matching `XdatUser`, creating one
  if absent. Encodes the `usernamePattern=[providerId]-[sub]`
  convention.

Two paths-in-the-Spring-chain decisions to figure out during
implementation: how to scope each filter to its path set (an
`AntPathRequestMatcher` per filter is the idiomatic Spring approach;
XNAT's path conventions are stable enough to enumerate), and where
in the existing XNAT filter chain to insert ours so they run after
session lookup but before the default anonymous principal is
attached. The current plugin inserts `OpenIdConnectFilter` after
`OAuth2ClientContextFilter`; ours likely sits before
`UsernamePasswordAuthenticationFilter`.

### 6.4 The audience-mismatch decision (token exchange in the plugin)

Keycloak access tokens carry an `aud` claim naming the client they
were issued for. A token issued for the `jupyterhub` client cannot
be straightforwardly presented to a service expecting `aud == xnat`.
There are three ways to handle this:

1. **Multi-audience mapper on the issuing client.** Configure the
   `jupyterhub` client to include `xnat` in its audiences via an
   "Audience" protocol mapper. The token is broadly usable; any
   service that trusts Keycloak with the right realm sees it as
   in-audience. Cheap to set up. Looser blast radius — every
   `jupyterhub`-issued token is implicitly an `xnat`-callable
   token, even if the user has no `xnat-access` role.
2. **Token exchange (RFC 8693) inside the plugin.** The plugin
   receives a `jupyterhub`-audience token, calls Keycloak's
   token-exchange endpoint server-to-server, and exchanges it for
   an `xnat`-audience token. Keycloak enforces the exchange — does
   the user have permission to act as `xnat`? Does `jupyterhub`
   have permission to exchange to `xnat`? — and the resulting
   `xnat`-audience token is what the plugin actually trusts.
3. **Relaxed audience check inside the plugin.** Plugin trusts any
   Scout-realm-issued token, regardless of `aud`, as long as the
   `xnat-access` role claim is present. Simplest. Requires careful
   role-claim review in the realm template since the role-claim
   becomes the entire gate.

**We're going with (2).** Rationale:

- The exchange is server-to-server inside the plugin. Notebook code
  doesn't change — the user still calls XNAT with their existing
  access token. End users never see the exchange mechanic.
- The exchange gives Keycloak a chance to enforce its own
  authorization model (token-exchange permissions, role
  requirements) at the moment of exchange. This is more defensible
  than trusting any Scout-issued token.
- Audit clarity: Keycloak knows that `xnat` is acting on behalf of
  `jupyterhub` for this user, in real time. With (1) or (3), that
  trail is muddier.
- It scales: the same exchange pattern works for any future Scout
  service that wants to call XNAT, without a realm-template change
  per service.

Trade-off: more configuration. Specifically, the `xnat` Keycloak
client needs to be permitted to exchange tokens issued to other
Scout clients, which is a permission set we haven't configured in
the realm template before. Worth scoping during implementation;
Keycloak's token-exchange config is documented but historically
quirky.

### 6.5 Token freshness / notebook UX

Keycloak access tokens default to 5-minute lifetime. A notebook
that runs for hours needs the access token in its environment to
refresh, or every API call past minute 5 fails. JupyterHub's
oauthenticator supports auto-refresh
(`auth_refresh_age`, default 5 minutes) and an opt-in
`refresh_pre_spawn = True` that guarantees a fresh token at pod
start. The Hub-side mechanism is well-documented.

What's less clear is the in-notebook UX:

- Does the notebook receive the access token via `auth_state_hook`
  → `spawner.environment[KEYCLOAK_ACCESS_TOKEN]`? (The standard
  pattern, but oauthenticator issue #314 records real cases of env
  vars not propagating; needs verification with `kubectl exec`.)
- Does the notebook get the token via a JupyterHub API call from
  Python (`/hub/api/user`) at the moment it's needed, instead of
  via env var?
- How do we communicate "your token is stale, restart the kernel"
  vs auto-refresh transparently?
- For very-long-lived activities (multi-hour batches), does
  oauthenticator's auto-refresh keep the in-pod env var current,
  or is the env var a snapshot from spawn time? (Probably the
  latter; auto-refresh is Hub-side.)

**Decision: leave the UX open and investigate during implementation.**
We don't have enough information to commit to a pattern. The
proposal stands either way — the question is whether notebooks see
"fresh access token always" or "snapshot at start, manual refresh
later." Either is workable; the second is simpler to implement and
might be acceptable depending on how long users typically run
notebooks against XNAT.

### 6.6 Realm-template changes

Smaller than the current state. Concrete deltas from §2.2:

- Keep: `xnat` client, `xnat-access` client role,
  `scout-user` → `xnat-access` mapping, `xnat-user` / `xnat-admin`
  cosmetic roles, the Group Membership protocol mapper (move it from
  the k3d `kcadm` step into the realm template).
- Drop: the `browser-xnat-access` authentication flow. The role
  check is now done in the plugin's bearer filter and via
  oauth2-proxy on the browser path; a Keycloak-side conditional
  flow override is no longer needed.
- Add: token-exchange permissions for the `xnat` client (see §6.4).

Net effect: the realm template moves closer to the shape every
other Scout service uses, with one extra item (the exchange
permission) that's specifically about the multi-service API access
story.

## 7. POC plan — phased breadcrumbs

Each phase ends with a verifiable outcome. The phases are ordered so
each builds on the previous and so the riskiest research-heavy bits
happen early.

### Phase A — XNAT plugin scaffolding (header-trust filter only)

Goal: prove the new plugin can replace the OIDC plugin for browser
auth, with oauth2-proxy in front.

- New plugin project (Java, Gradle, packaged as XNAT plugin JAR).
  Use the existing `openid-auth-plugin` repo as a structural
  reference: same Gradle layout, same `BaseXnatSecurityExtension`
  pattern. Don't fork; start fresh.
- Implement `HeaderTrustFilter` and `UserProvisioningService`.
- Wire oauth2-proxy in front of XNAT in the deployment values:
  Traefik middleware, oauth2-proxy config, `xnat` Keycloak client
  configured for oauth2-proxy's redirect URI.
- Validate with the same agent-browser harness as the first POC
  (alice in, bob out), but this time the gate is oauth2-proxy +
  the role claim, not Keycloak's custom flow.

Research breadcrumbs for whoever picks this up:

- Read `OpenIdSecurityExtension.java` in the existing plugin —
  it's 30 lines and shows the seam.
- Spring's `AntPathRequestMatcher` for filter scoping.
- oauth2-proxy's `--set-xauthrequest=true` and which headers
  it forwards. The Scout `oauth2-proxy` role's
  `authResponseHeaders` block is the existing list.

### Phase B — Bearer-token filter with token exchange

Goal: a notebook user with an existing Keycloak access token can
call XNAT and it works.

- Implement `BearerTokenFilter` in the same plugin.
- Implement Keycloak token-exchange call inside the filter (or in
  a small `TokenExchangeService` it depends on). Cache the
  resulting tokens briefly.
- Configure the Keycloak realm template for the exchange (xnat
  client allowed to exchange from jupyterhub etc.).
- Test directly with `curl` from a script using a test access token
  before involving Jupyter.

Research breadcrumbs:

- Keycloak token-exchange docs: feature is off by default per
  realm; permissions are configured per-client-pair under "Client
  Permissions." Historically required the `preview` profile and
  `--features=token-exchange` server flag.
- JWKS caching: Keycloak's JWKS endpoint is at
  `/realms/scout/protocol/openid-connect/certs`. Standard JWT
  libraries (Nimbus JOSE+JWT, jjwt) can cache JWKS automatically.
- The XNAT plugin's existing JWKS use (`OpenIdApi.java` exposes
  `/openid/.well-known/jwks.json` outbound) is a different
  direction — outbound JWKS, not inbound JWT validation. Don't
  confuse the two.

### Phase C — Jupyter integration

Goal: a notebook spawned by Scout's JupyterHub has the Keycloak
access token in its environment and can call XNAT.

- Add a JupyterHub `auth_state_hook` to Scout's `jupyter` role.
  The hook reads `auth_state` and writes `KEYCLOAK_ACCESS_TOKEN`
  (and probably `KEYCLOAK_REFRESH_TOKEN`) to `spawner.environment`.
  Set `c.GenericOAuthenticator.enable_auth_state = True` (already
  set) and `refresh_pre_spawn = True`.
- Verify env-var propagation by `kubectl exec`-ing into a spawned
  notebook pod and checking `printenv KEYCLOAK_ACCESS_TOKEN`. The
  agent research notes there are real oauthenticator issues
  (#314) where this hasn't always propagated cleanly.
- Add a small Scout sample notebook that calls XNAT's
  `/data/projects` endpoint using `os.environ["KEYCLOAK_ACCESS_TOKEN"]`.
  Confirm the XNAT side sees the user, provisions on first call,
  applies the role gate.

Research breadcrumbs:

- `oauthenticator.generic.GenericOAuthenticator` `auth_state` shape
  isn't formally documented. Inspect what Keycloak actually returns
  via a debug print in the `auth_state_hook`. Likely includes
  `access_token`, `refresh_token`, `scope`, `token_response`, and
  some form of decoded userinfo.
- JupyterHub Spawner docs on `auth_state_hook` and `pre_spawn_hook`.
- The token-freshness UX question from §6.5 is open during this
  phase. Default to "snapshot at spawn time"; revisit if it's
  actually painful.

### Phase D — End-to-end assertions

Replace / extend the existing agent-browser test with three
assertions:

- alice (browser) → logs in via oauth2-proxy → lands on XNAT
  homepage authenticated.
- bob (browser) → blocked by oauth2-proxy / role check → never
  reaches XNAT.
- alice (notebook) → calls XNAT's REST API with her Keycloak
  access token from `os.environ` → gets a 200, sees her own user.
- (Optional fourth) bob (notebook) → calls XNAT's REST API → gets
  a 403 from the role check, with a useful error message in the
  logs.

This is the "the next POC works" definition.

## 8. Out of scope for this proposal

Recorded so they're not lost; not part of the next POC.

- **DICOM ingest.** DIMSE C-STORE is raw TCP and isn't behind any
  HTTP ingress. DICOMweb is HTTP and would ride the same path as
  the API ingress; whether it goes through oauth2-proxy or
  bypasses-with-bearer is a sub-question for whoever brings up
  DICOM. Out of scope here.
- **Back-channel logout.** XNAT 1.x and the existing plugin don't
  expose a back-channel logout endpoint. Adding one (so signing out
  of launchpad immediately ends the XNAT session everywhere) means
  another plugin filter that validates `logout_token` JWTs from
  Keycloak and invalidates matching XNAT sessions. Worth doing
  later; doesn't block the API access goal.
- **Air-gapped delivery of the new plugin.** The init-container-
  downloads-from-Bitbucket pattern is internet-only. The air-gap
  story for any plugin (existing or new) is to vendor the JAR —
  via Nexus per the existing air-gap ADR, a `binaryData`
  ConfigMap, or a tiny installer image. This problem is the same
  whether the plugin is the old one or the new one; not specific
  to this proposal.
- **On-prem storage / multi-node PV story.** Same as today.
  The XNAT chart's PVCs are RWX-defaulted; the k3d slice uses
  `local-path` RWO and accepts the node-pinning consequence.
  Production on-prem will need either networked storage or
  documented pinned-node recovery procedures. Orthogonal to auth.
- **`xnat-web` multi-arch image.** Apple Silicon hosts can't run
  the current image at usable speed. Upstream concern; we're not
  going to paper over it from the deploy side.
- **Mail wiring.** Postfix is deployed but not connected to
  anything. Pointing it at MailHog (or pointing XNAT directly at
  MailHog) is a follow-up.
- **Replacing or forking the upstream Helm chart.** The three
  workarounds in §4.4 are paper-over-from-values; eventually they
  should be reported / fixed upstream or lead to a fork. Not part
  of the auth POC.

## 9. Open questions for the team

These are decisions we should reach alignment on before Phase A
starts.

1. **Keycloak realm template vs. Ansible task for the
   token-exchange permissions.** Realm template is cleaner if
   `keycloak-config-cli` handles per-client-pair exchange
   permissions reliably; otherwise an Ansible task is the fallback.
   Worth a small spike before committing.
2. **One ingress with path-based routing, or two ingresses?**
   "Browser through oauth2-proxy / API direct" can be one ingress
   with Traefik path matchers selecting the middleware, or two
   `Ingress` resources (`xnat.hostname` for browser,
   `xnat-api.hostname` for API). The first is closer to how Scout
   does things; the second is operationally simpler. Either works.
3. **Do we want oauth2-proxy in front of API paths too, with
   `--skip-jwt-bearer-tokens`?** The agent research confirmed the
   feature exists, but the cross-client audience matching is
   strict. With token exchange happening in the plugin we don't
   need oauth2-proxy on the API path, so the simpler answer is
   "no, bypass it." Worth confirming we agree before building.
4. **Token freshness UX in notebooks** (per §6.5). Decide during
   Phase C; this is a "find out by trying" question.
