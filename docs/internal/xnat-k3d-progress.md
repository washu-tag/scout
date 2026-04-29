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

**Current status:** steps 1–10 pass. Step 11's positive assertion (alice
gets in) is the part that doesn't pass yet — see the next section for why
and what we have to decide.

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
the `xnat-plugin-keycloak` Secret).

The properties file currently sets:

- `auto.enabled=true` — supposed to auto-enable Keycloak-provisioned users
  on first login (so admins don't have to click-approve each one).
- `forceUserCreate=true`, `auto.verified=true` — should provision new XNAT
  users on first login.
- `openid.keycloak.allowedGroups=scout-user` — supposed to gate users on
  the `groups` claim.

This is where we're stuck. After a complete OIDC round trip:

- The plugin shows up correctly in `/xapi/plugins` and the
  `/openid-login?providerId=keycloak` route 302s to Keycloak as expected.
- Both alice and bob complete Keycloak login and the callback hits XNAT
  with a valid auth code.
- Neither user is created in XNAT. `/xapi/users` still returns just
  `["admin", "guest"]` after both logins.
- The plugin's debug log only contains the redirect-prep trace ("At
  create rest template", "Provider id is keycloak", "PKCE Enabled"). No
  callback-side activity (token exchange, userinfo, user create) is
  logged.

In other words: the plugin is loaded and the redirect path works, but the
callback path is silently failing somewhere inside the plugin or its
Spring filter chain — `forceUserCreate` doesn't seem to fire and
`allowedGroups` is never reached because no user gets that far.

The runbook anticipated *part* of this — it warned that 1.4.1-xpl might
not honor `allowedGroups`, in which case bob would slip in as a
disabled user. What we're seeing is broader: even alice never gets in,
which suggests the plugin's user-provisioning path isn't running at all.

### Layer 3 — XNAT site config

XNAT has a separate, site-wide list of enabled auth providers. Chart
defaults leave `enabledProviders=["localdb"]`, which means the keycloak
provider isn't eligible at all even when the plugin and properties are
correct. We now POST `["localdb", "keycloak"]` to `/xapi/siteConfig` in
step 10 of `run-all.sh` so this is set every run.

### What we still have to decide

Two reasonable next moves; we should pick one rather than try both.

**Option A — Keep authorization in the plugin (current shape).** Figure
out why 1.4.1-xpl's callback path isn't running. Likely sub-tasks:
decompile `OpenIdAuthPlugin.class` or pull a different plugin version,
confirm the actual property names the build honors, compare against
1.4.1-xpl's own README, and either fix our properties file or pin a known
working version. Lowest blast radius; keeps existing config patterns; but
puts us on the hook for plugin internals.

**Option B — Move authorization into Keycloak (runbook's "Plan B").**
Attach a required `xnat-access` realm role to the xnat client, map it
from `scout-user` group membership, and let Keycloak refuse bob outright
at the auth endpoint. The XNAT plugin then only has to provision users
that Keycloak already approved. Sidesteps the `allowedGroups` and
user-provisioning issues entirely; matches how Scout already gates other
services. Requires editing the realm template (more visible blast radius
to dev03) and may still leave a residual question about whether 1.4.1-xpl
provisions users at all, which Option A would have to answer regardless.

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
