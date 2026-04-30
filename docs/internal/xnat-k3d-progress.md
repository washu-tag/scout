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

**Current status:** all steps pass. Alice (in `scout-user`) completes the
OIDC round trip, is provisioned in XNAT as `keycloak-<sub>`, and lands on
the authenticated homepage. Bob (not in `scout-user`) is rejected by
Keycloak's deny-access endpoint before any code runs at the xnat client,
because the xnat client's browser flow is overridden with a conditional
sub-flow that requires the `xnat-access` client role. The full reset →
rerun loop also works (see "Re-running the test" below).

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

### How we landed: Option B (Keycloak-side gate)

We took the runbook's "Plan B" — move authorization into Keycloak
because the plugin source confirms 1.4.1-xpl has no group-filter code
to leverage. Concrete pieces, all wired up by `run-all.sh` step 5:

1. **`xnat-access` client role** on the `xnat` Keycloak client. Defined
   in `scout-realm.json.j2` (next to the existing `xnat-user` /
   `xnat-admin` cosmetic roles), so it lands automatically on fresh
   realm imports.
2. **`scout-user` group → `xnat-access` mapping**, also in the realm
   template. Members of `scout-user` get the role transitively.
3. **`browser-xnat-access` authentication flow**, copied from the
   built-in `browser` flow at runtime via `kcadm`. We add a
   `CONDITIONAL` sub-flow inside its `forms` step, with two `REQUIRED`
   executions:
   - `Condition - User Role`, configured with `condUserRole=xnat.xnat-access`
     and `negate=true` (i.e., "fire when the user is *missing* the role").
   - `Deny Access`, which terminates the flow with the access-denied
     screen.
4. **Client-level binding override** — the `xnat` client's
   `authenticationFlowBindingOverrides.browser` is set to the new flow's
   ID. This scope-limits the gate: only logins via the xnat client see
   it; the rest of the realm's clients keep the standard browser flow.

Result: alice (in `scout-user`, has `xnat-access`) sees the regular
Keycloak login form, authenticates, and the OIDC callback hits XNAT
which provisions her. Bob (not in `scout-user`, no `xnat-access`)
authenticates against Keycloak but is intercepted by the conditional
sub-flow — Keycloak shows its access-denied page and never issues an
auth code, so the XNAT pod never sees bob at all.

The kcadm calls in `run-all.sh` are written idempotently (skip-if-exists
checks on the role, flow, sub-flow, and child executions), so reruns
against the same cluster don't double-up the configuration.

### Re-running the test

`xnat/k3d/reset-test-state.sh` resets just enough state to drive
`test-login.sh` again without recreating the cluster:

- Logs out alice and bob in Keycloak (so leftover SSO cookies don't
  short-circuit the next login form).
- Deletes the `keycloak-*` users from XNAT directly via `psql` on
  `xnat-postgres-1`. XNAT's REST API only supports disabling users
  (PUT `enabled=false` returns 200; DELETE returns 405); a disabled
  user blocks the openid plugin's user-creation path on the next login,
  so SQL deletion is the only way to fully exercise provisioning again.
- Closes `agent-browser` sessions named `login-alice` / `login-bob`.

For a full teardown (Keycloak, Postgres, the cluster itself) use
`xnat/k3d/teardown-cluster.sh` and then `run-all.sh`.

### What this leaves for dev03

The role + group mapping in `scout-realm.json.j2` is durable and will
land on dev03 the next time the realm is imported. The `kcadm` build of
the `browser-xnat-access` flow is currently k3d-only — it lives in
`run-all.sh` rather than in the Ansible role. Promoting Plan B to dev03
means either (a) lifting the kcadm sequence into a small Ansible task
under the `keycloak` role, or (b) expressing the flow override directly
in `scout-realm.json.j2` so keycloak-config-cli imports it. Both are
feasible; (b) is cleaner if keycloak-config-cli's flow-import support
handles conditional executions reliably.

We should make this call before iterating further on `run-all.sh`.

## Modifications to Scout (not counting one-off playbooks)

This list is for things that aren't k3d-only — anything that affects
shared roles, shared values, or behavior dev03 will also see.

- **`xnat/xnat-values.yaml`** — added
  `cnpg.external.postgresqlPort: 5432`. This is a no-op semantically
  (the value the chart would have rendered anyway), but it works around
  the upstream chart bug described in the next section. dev03 sees the
  same fix.
- **`ansible/roles/keycloak/templates/scout-realm.json.j2`** — added
  the `xnat-access` client role on the xnat client and mapped it from
  the `scout-user` group's `clientRoles`. This is the durable half of
  Option B; the matching `browser-xnat-access` authentication flow is
  k3d-only at the moment (built via `kcadm` in `run-all.sh` step 5)
  and would need to be promoted to the role / realm template before
  dev03 sees end-to-end gating.

The k3d-only artifacts (`ansible/playbooks/dev-tls.yaml`,
`ansible/playbooks/auth-keycloak-only.yaml`, `ansible/inventory.k3d.yaml`,
the `xnat/k3d/` tree) are deliberately scoped to local-cluster testing
and don't affect dev03.

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

## Apple Silicon (arm64) — XNAT image runs under qemu and is unusably slow

This does not contradict the "all steps pass" status above; that result
stands. It was produced on a host where the `xnatworks/xnat-web` image runs
natively. A subsequent attempt to re-run `run-all.sh` on an arm64 Mac
(host `uname -m` = `arm64`) ran into a separate, platform-level problem
worth recording so the next person doesn't lose an hour to it.

**Symptom:** `run-all.sh` failed at step 10 with
`Error: context deadline exceeded` from `helm install --wait`, after the
20-minute timeout the script already passes. The `xnat-0` pod was still
`Running` (one early restart from a startup-probe miss, then alive), but
Tomcat had logged nothing past `TldScanner.scanJars` at `T+1:00` and the
"set the node ID to xnat-0" access-log line at `T+2:00` — silent for
40+ minutes after that, with the JVM pinning a CPU the entire time.

**Root cause:** `xnatworks/xnat-web:1.9.2.1` is a single-arch image
(`linux/amd64`). On arm64 hosts, k3d/containerd transparently runs it
through `qemu-x86_64` user-mode emulation. Inside the pod:

```
$ kubectl -n xnat exec xnat-0 -c xnat -- uname -m
x86_64
$ kubectl -n xnat exec xnat-0 -c xnat -- ps -ef | head
tomcat  1  0 99 ... /usr/bin/qemu-x86_64 /opt/java/openjdk/bin/java ...
```

The wrapping `/usr/bin/qemu-x86_64` in front of `java` is the giveaway.
Webapp deployment + Hibernate `hbm2ddl.auto=update` under user-mode
emulation runs roughly an order of magnitude slower than native, which
puts first boot well past the 10-minute startup-probe budget the script
already bumps to and well past helm's 20-minute `--wait`.

**Why the runbook's existing `failureThreshold=60` workaround isn't
enough:** that workaround sizes for a slow-but-native first boot (3–5
min). Under qemu emulation the cost isn't a constant; it scales with
how much bytecode the JVM has to JIT, which is most of XNAT on cold
start. We saw 40+ minutes of CPU-bound silence with no progress
markers, and there's no reason to believe it would have terminated in
a useful timeframe.

**What this means for the laptop test plan:**

- The k3d slice as written is fine on x86_64 hosts (Linux laptops,
  Intel Macs, dev VMs). The "all steps pass" result above came from
  one of those.
- On Apple Silicon the durable fix is a multi-arch xnat-web image
  (or a separate arm64 build). That's an upstream concern, not
  something `run-all.sh` should paper over.
- Until that exists, the realistic options on an arm64 laptop are:
  (a) skip the local k3d run and validate on a real x86_64 cluster,
  (b) run the k3d slice inside an x86_64 Linux VM so containerd
  schedules the image natively, or (c) accept very long runtimes and
  bump both the startup probe and helm `--wait` to >1 hour. None of
  these are pleasant; (a) is what we'll do once dev03 is back.
- No code/config change has been made in response to this finding —
  it's purely a host-platform compatibility note. The cluster from
  the failed run was left up at the time of writing in case anyone
  wants to keep poking at it; otherwise `teardown-cluster.sh` cleans
  it up.

State of the failed run, for context:

- Phases 1–9 all succeeded (cluster, TLS, Postgres, Keycloak with the
  realm import, `scout-user` group + alice/bob + `xnat-access` role +
  `browser-xnat-access` flow override, manifests, namespace, secrets,
  `helm dependency update`).
- Phase 10 helm install hit `context deadline exceeded` after 20 min;
  `xnat-0` was `Running` but stuck booting under qemu. `xnat-postgres`
  and `xnat-mail` were both healthy.
- Phase 11 (the agent-browser login assertions) never ran.

## Next step

Re-run on dev03 once the server issues there are resolved. The Ansible
inventory, role changes, and realm template additions described above
all carry over directly; the only k3d-only piece that doesn't (the
`browser-xnat-access` flow built via `kcadm` in `run-all.sh` step 5)
needs to be promoted to either an Ansible task or the realm template
before dev03 sees end-to-end gating — see "What this leaves for dev03"
above.

## Pointers

- Runbook (the canonical phase-by-phase doc): `xnat-k3d-runbook.md`.
- Driver script: `xnat/k3d/run-all.sh`.
- Login assertions: `xnat/k3d/test-login.sh`.
- dev03 deployment plan and runbook (parent docs):
  `xnat-deployment-plan.md`, `xnat-deployment-runbook.md`.
