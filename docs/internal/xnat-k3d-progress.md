# XNAT on Local k3d — Progress Notes

Companion to `xnat-k3d-runbook.md`. Captures where the local k3d slice
currently stands, the open decisions about how login is configured, and the
edits we've made to Scout's Ansible / charts / values to get this far —
plus upstream XNAT chart bugs we're papering over rather than fixing.

The goal here is documentation, not a pitch: if you read just this file and
the runbook, you should know what's working, what isn't, and what we still
have to decide before the runbook proves dev03 readiness.

## What we're testing and how

The k3d slice exercises one specific question end-to-end: **can a user log
into XNAT through Keycloak, gated on Scout group membership?** Everything
else in the Scout platform (lake, analytics, monitoring, oauth2-proxy,
ingest pipelines, multi-replica HA, air-gapped delivery) is intentionally
out of scope here — the dev03 runbook covers those.

The scope on the cluster side:

- A single-node `k3d` cluster on the laptop, with `:80`/`:443` mapped to
  the load balancer and TLS via `mkcert`-issued `*.localtest.me` certs.
- CloudNativePG (CNPG) for the Keycloak database.
- Keycloak + the Scout realm template, imported via keycloak-config-cli.
- A second CNPG cluster spun up by the XNAT helm chart for XNAT's own
  database (separate from Scout's central Postgres).
- The XNAT helm chart with the `openid-auth-plugin` installed at runtime
  via an init container, the JVM truststore extended to trust mkcert's
  root CA, and `prefs-init.ini` mounted to skip the first-boot setup
  wizard.

The scope on the test side:

- `kcadm` provisions the `scout-user` group, two users (`alice` in
  `scout-user`, `bob` outside), and a Group Membership protocol mapper
  on the `xnat` Keycloak client so the `groups` claim shows up in
  ID/access tokens and userinfo responses.
- `agent-browser` drives two OIDC logins through Chromium and asserts on
  the outcome:
  - **alice** (in `scout-user`) → expected to land on the XNAT homepage
    authenticated.
  - **bob** (not in `scout-user`) → expected to be rejected at the OIDC
    callback.

`xnat/k3d/run-all.sh` automates everything from cluster creation through
the agent-browser assertions, with health waits between phases and a
non-zero exit on first failure. The runbook documents the same flow as
discrete numbered phases so individual steps can be re-run when debugging.

**Current status:** steps 1–10 pass. Alice's positive assertion now
passes too — she completes the OIDC flow, is provisioned as
`keycloak-<sub>`, and lands on the authenticated XNAT homepage. Bob's
negative assertion does *not* pass — he is also let in, because the
plugin we're using has no group-filter support at all (see Layer 2
below). Step 11 therefore fails on bob.

## Login configuration: choices made and choices still pending

The integration has three layers, each with its own gating model. Picking
where authorization lives is the central design choice.

### Layer 1 — Keycloak realm + xnat client

Decided already, no open questions:

- Scout realm imported from the existing `scout-realm.json.j2` template,
  which now includes the `xnat` client.
- `xnat` client uses the OIDC redirect/code flow with PKCE. Direct access
  grants are off in production.
  - The k3d test toggles `directAccessGrantsEnabled=true` on the client
    via `kcadm` solely so `run-all.sh` can do a password-grant smoke test
    of alice's `groups` claim without driving a browser. This is local to
    the k3d run; the realm template is unchanged.
- A Group Membership protocol mapper emits `groups` in the ID token, the
  access token, and userinfo. Verified working: alice's id_token contains
  `groups: ["scout-user"]`, bob's contains `groups: []`.

### Layer 2 — XNAT openid-auth-plugin

Plugin: `openid-auth-plugin-1.4.1-xpl.jar`, downloaded at startup by an
init container. Provider properties live at
`/data/xnat/home/config/auth/keycloak-provider.properties` (mounted from
the `xnat-plugin-keycloak` Secret). Source for the plugin lives at
`/Users/jflavin/repos/openid-auth-plugin` for reference.

What the plugin **does** support, all confirmed working:

- `auto.enabled=true` — auto-enables Keycloak-provisioned users on first
  login (no admin click-approval needed).
- `forceUserCreate=true`, `auto.verified=true` — provision new XNAT users
  on first login.
- `usernamePattern=[providerId]-[sub]` — alice now appears in `/xapi/users`
  as `keycloak-<sub>` after a successful login.
- `shouldFilterEmailDomains` + `allowedEmailDomains` — domain whitelist on
  the email claim.

What the plugin **does not** support, confirmed by reading the source:

- `allowedGroups` does not exist in 1.4.1-xpl. Grepping the source for
  `allowedGroups` / `allowed_groups` / `allowed.groups` returns nothing.
  The runbook's `openid.keycloak.allowedGroups=scout-user` line is
  silently ignored — there's no code path in the plugin that reads the
  `groups` claim or filters on it.

So with this plugin alone we can let users in (alice ✓) and create them
on the fly, but we can't keep specific Keycloak users out of XNAT (bob ✗).
The email-domain filter is the only filter the plugin offers, and it
won't help here because alice and bob share the `localtest.me` domain.

A separate gotcha worth recording, since it will trip up any future
debugging: the plugin bundles a logback config that sets the
`au.edu.qcif.xnat.auth.openid` package to **INFO** and only the
`OpenIdAuthPlugin` class to **DEBUG**. So all the debug-level logging in
`OpenIdConnectFilter` (which does the actual callback work — token
exchange, userinfo, user lookup, user creation) is silently dropped. To
trace callback failures you have to add an additional logback config or
patch the plugin's bundled one.

### Layer 3 — XNAT site config

XNAT has a separate, site-wide list of enabled auth providers. Chart
defaults leave `enabledProviders=["localdb"]`. Two things to know:

- The login page renders providers off this list, so `keycloak` has to
  be in it for the "Sign in with Scout" link to even be a candidate
  (we don't render it on the homepage anyway — see the runbook — but
  the underlying provider has to be enabled here regardless).
- The plugin's own `OpenIdConnectFilter.attemptAuthentication()` calls
  `_plugin.isEnabled(providerId)` against a *different* list — the one
  the plugin itself builds from `auth/*-provider.properties` files. So
  this site-config flag is **necessary but not sufficient** for the
  callback path; passing the site-config check just means you've cleared
  the gate that controls login-page rendering.

We POST `["localdb", "keycloak"]` to `/xapi/siteConfig` in step 10 of
`run-all.sh` so this is set every run.

### Layer 4 — Pod-side DNS for `keycloak.localtest.me`

Not really a config layer, but the missing piece that masked the rest of
this analysis for several iterations. `keycloak.localtest.me` resolves to
`127.0.0.1` via public DNS. From the user's host that's the k3d load
balancer; from inside a pod that's the pod's own loopback. The XNAT pod
calling `https://keycloak.localtest.me/.../token` (token exchange) and
`/userinfo` got "Connection refused" before any plugin logic ran, the
filter caught the OAuth2Exception, redirected to `/app/template/Login.vm`,
and (per the logback gotcha above) wrote nothing to `openid.log` — so the
failure looked like "user creation isn't running" when it was really
"the pod can't reach Keycloak."

Fix: install a CoreDNS `*.server` override in the `coredns-custom`
ConfigMap that points `keycloak.localtest.me` at Traefik's cluster IP,
so in-pod calls hit Traefik (which terminates TLS with the `*.localtest.me`
cert and routes via the Keycloak Ingress). `run-all.sh` does this between
phases 4 and 5 by reading `kubectl -n kube-system get svc traefik`.

The Scout `k3s` role exposes `coredns_extra_server_blocks` for exactly
this kind of override on dev03, but the k3d slice deliberately skips
`playbooks/k3s.yaml` (we use `k3d`, not `k3s.yaml`-managed k3s), so
`run-all.sh` writes the same `coredns-custom` ConfigMap shape directly.
Same shape as what the k3s role would have produced; just inline.

### What we still have to decide

Reading the plugin source closes the original Option A from this doc:
there is no group-gating code path in 1.4.1-xpl to chase. The realistic
shortlist is now:

**Option B — Move authorization into Keycloak (runbook's "Plan B").**
Attach a required `xnat-access` realm role to the `xnat` client, map it
from `scout-user` group membership, and let Keycloak refuse bob outright
at the auth endpoint. The XNAT plugin then only has to provision users
that Keycloak already approved. Matches how Scout already gates other
services and is independent of which plugin version we settle on. Cost:
edits to `scout-realm.json.j2` so dev03 sees it too.

**Option C — Patch the plugin** to honor `allowedGroups` (or similar),
and ship the patched jar via the init container (or a Harbor mirror in
air-gapped environments). Lowest visible config-surface change, but puts
us on the hook for maintaining a fork of an external plugin.

**Option D — Drop the alice/bob group gating from this slice.** Accept
that the k3d test only proves "OIDC users can log in," not "non-Scout
users are kept out," and pick a real strategy when the answer matters.
Use this if the gate isn't a release blocker for dev03.

Recommendation: Option B. Same reasoning as the runbook — Scout already
gates other services this way, so it's a pattern the team knows, and it
works regardless of plugin version. Defer the call to John.

We should make this call before iterating further on `run-all.sh`.

## Modifications to Scout (not counting one-off playbooks)

This list is for things that aren't k3d-only — anything that affects
shared roles, shared values, or behavior dev03 will also see.

- **`xnat/xnat-values.yaml`** — added
  `cnpg.external.postgresqlPort: 5432`. This is a no-op semantically
  (the value the chart would have rendered anyway), but it works around
  the upstream chart bug described in the next section. dev03 sees the
  same fix.

The k3d-only artifacts (`ansible/playbooks/dev-tls.yaml`,
`ansible/playbooks/auth-keycloak-only.yaml`, `ansible/inventory.k3d.yaml`,
the `xnat/k3d/` tree including `test-login.sh`) are deliberately scoped
to local-cluster testing and don't affect dev03.

## Upstream XNAT helm chart bugs we're working around

Three issues in `helm-charts/helm/xnat` (1.0.1 / appVersion 1.9.2.1) that
we paper over from the values/run-all side rather than patching the chart.
They should be reported / fixed upstream eventually.

1. **`templates/_postgresql.tpl` — broken whitespace trimmer in the
   default postgres port.** When `cnpg.external.postgresqlPort` is empty,
   the template hits an `else` branch that emits `"5432"` on its own line
   with no `{{-` trimmer. The output is `\n"5432"\n`, which lands at
   column 0 inside the rendered `wait-for-postgres` block scalar in
   `statefulset.yaml`. That breaks the YAML block scalar and `helm
   install` fails with `error converting YAML to JSON: yaml: line 114:
   could not find expected ':'`.

   Workaround: set `cnpg.external.postgresqlPort: 5432` in
   `xnat-values.yaml` so the chart hits the (clean) `if` branch.

2. **Chart pulls in the `bokysan/postfix` mail subchart unconditionally
   and requires a `postfix-password` Secret.** There's no `mail.enabled`
   flag and the secret name is hardcoded. Without the secret, the
   `xnat-mail-0` pod sits in `CreateContainerConfigError` indefinitely,
   which means `helm install --wait` never returns even though XNAT
   itself is healthy.

   Workaround: `run-all.sh` step 8 creates a placeholder
   `postfix-password` Secret in the `xnat` namespace. We don't actually
   use mail (`prefs-init.ini` sets `smtpEnabled=false`), so the secret's
   contents are arbitrary.

3. **Default startup probe is too tight for first-boot Hibernate DDL.**
   The chart sets `probes.startup.failureThreshold=15` with
   `periodSeconds=10`, giving Hibernate 150s to finish creating the
   schema. On a laptop k3d this consistently fails; XNAT first boot
   takes 3–5+ minutes.

   Workaround: `run-all.sh` passes
   `--set probes.startup.failureThreshold=60` (10 min) and bumps
   `--timeout` to 20m. The runbook §10 already mentioned this would be
   needed; we've now wired it into the script so it's not a manual step.

## Pointers

- Runbook (the canonical phase-by-phase doc): `xnat-k3d-runbook.md`.
- Driver script: `xnat/k3d/run-all.sh`.
- Login assertions: `xnat/k3d/test-login.sh`.
- dev03 deployment plan and runbook (parent docs):
  `xnat-deployment-plan.md`, `xnat-deployment-runbook.md`.
