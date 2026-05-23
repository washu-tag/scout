# Trino RBAC Implementation Plan

Scout uses Ansible-driven Helm deployments. Trino is the user-facing data plane in `scout-analytics`; the Keycloak realm is owned by `keycloak-config-cli` (full-replace JSON); a Keycloak SPI plugin already exists at `keycloak/event-listener/` (Java/Gradle, packaged into a custom Keycloak image). This plan reuses these patterns rather than introducing new operational surfaces.

Phases are PR-shaped work units within a single coordinated release, not deployment phases. Scout deploys this as a **full reinstall**: every client role flips to the new auth model in one release. There is no flag-gated rollout, no dual-auth window, no separate cutover phase. The shared `trino` user is removed in the same release that introduces JWT auth.

Companion to [ADR 0020](adr/0020-trino-rbac-architecture.md). Working doc, expected to evolve as implementation surfaces unknowns.

---

## Phase 0: Keycloak prerequisites (realm, clients, attribute schema)

**Scope.** Realm declares the three service clients (`superset_svc`, `jupyter_svc`, `openwebui_mcp_svc`), declares two authorization user attributes (`allowed_facilities` multivalued, `mask_phi_fields` boolean) as managed attributes in the user profile, declares a `trino-audience` client scope that adds `aud=trino` to access tokens issued to these clients, and adds a JWT claim mapper for `allowed_facilities` (used by the Trino audit event listener for tenant tagging). The Keycloak admin API gets a dedicated service account (client `opa-keycloak-reader` with realm-management role `view-users`) for OPA's attribute fetch. The existing `scout-admin` and `scout-user` groups are reused — no new groups. No runtime data plane change yet.

**Identity model.** Group membership in `scout-admin` or `scout-user` gates which users get the OPA policy applied (i.e., "this is an authenticated Scout user"). Per-user attributes determine *all* data-access decisions for both groups — there is **no group-based bypass**. `scout-admin` exists as a Keycloak-level marker for users who can manage other users' attributes (and other admin operations across Scout apps); for Trino RBAC purposes, admin and user are treated identically. An admin who wants unrestricted access sets their own attributes to `allowed_facilities: ["*"]` + `mask_phi_fields: "false"` — the same opt-in that any user with a multi-site research need would take. This makes testing trivial: an admin can experiment with restricted views by toggling their own attributes.

Attributes:
- `allowed_facilities` — multivalued, list of `sending_facility` codes the user can read. Special wildcard value `*` means "all facilities."
- `mask_phi_fields` — boolean (`"true"`/`"false"`). Default unset → treated as `true` (mask). Setting to `"false"` grants clear-text access to PHI columns.

**Files.**
- `ansible/roles/keycloak/templates/scout-realm.json.j2` — add four clients (`superset_svc`, `jupyter_svc`, `openwebui_mcp_svc`, `opa-keycloak-reader`), all `clientAuthenticatorType=client-secret`, `serviceAccountsEnabled=true`, `standardFlowEnabled=false`, `directAccessGrantsEnabled=false`. Each service client gets `defaultClientScopes` including the new `trino-audience` scope. The reader client gets a `service-account-realm-management:view-users` mapping. Add a `trino-audience` block under `clientScopes` with a hardcoded-audience protocol mapper (`oidc-audience-mapper`, `included.custom.audience=trino`, `access.token.claim=true`). Add `allowed_facilities` (multivalued) and `mask_phi_fields` (single-valued) to the `userProfile.attributes` list with `permissions.view/edit: [admin]` and `group: scout`. Add a protocol mapper on `microprofile-jwt` emitting `allowed_facilities` as a `scout_allowed_facilities` claim (used only by the Trino audit event listener; OPA fetches both attributes directly via `http.send` and does not rely on JWT claims). Existing `scout-admin` and `scout-user` group definitions remain untouched.
- `ansible/roles/keycloak/defaults/main.yaml` — new vars: `keycloak_superset_svc_client_id`, `keycloak_superset_svc_client_secret`, `keycloak_jupyter_svc_client_id`, `keycloak_jupyter_svc_client_secret`, `keycloak_openwebui_mcp_svc_client_id`, `keycloak_openwebui_mcp_svc_client_secret`, `keycloak_opa_reader_client_id`, `keycloak_opa_reader_client_secret`, `keycloak_trino_access_token_lifespan` (recommend 1800 = 30 min; see Risks). Wire secrets through the vault as the existing clients do.
- `ansible/group_vars/all/secrets.yaml` (or whichever vault holds existing `keycloak_*_client_secret` values) — add the four new secrets.

**Validation.**
- `keycloak-config-cli` re-run is idempotent. After redeploy: `curl -u opa-keycloak-reader:<secret> https://keycloak.../realms/scout/protocol/openid-connect/token -d grant_type=client_credentials` returns a token whose decoded `aud` includes `trino` and which can `GET /admin/realms/scout/users?username=<known-user>`.
- `kcadm.sh get users/<id>` for an existing test user shows the four new attribute groups exist and accept multi-valued writes.

**Rollback.** Revert the realm template; rerun `configure.yaml`. Service accounts are unused by anything else at this phase, so removing them is safe.

**Dependencies.** None. Keycloak 26.6.1 is already current.

**Effort.** 1 PR, ~1 day. Most cost is realm-JSON authoring + verifying full-replace doesn't drop any of the existing 7 clients.

---

## Phase 1: Trino auth wiring (JWT required)

**Scope.** `trino-analytics` coordinator enables JWT authentication on the chart's HTTPS listener (port 8443, cert from cert-manager). External clients must present a valid Keycloak-issued JWT with `aud=trino`. The chart's HTTP listener (port 8080) remains enabled for worker→coordinator internal-communication (authenticated via `internal-communication.shared-secret`) and the chart's pod probes (`/v1/info` is unauthenticated in Trino). This dual-listener pattern is the Trino chart's documented design — HTTPS-only is not supported by the chart (probes hardcode the HTTP port, the Service template always emits an http port entry). External access to port 8080 is mitigated by a NetworkPolicy restricting it to in-cluster Trino-pod traffic. The Trino web UI is not exposed externally — admin access via `kubectl port-forward` to the coordinator's 8443. The shared `trino` user default is removed; this PR is one of the work units that comprises the coordinated release.

The TLS cert is issued by **cert-manager** (already deployed in Scout per `ansible/roles/cassandra/tasks/deploy.yaml`). A `Certificate` resource with `spec.keystores.pkcs12.create=true` produces a Secret containing `keystore.p12` and the CA bundle (`ca.crt`). The chart mounts the Secret; clients mount the CA bundle from a separate ConfigMap (produced by cert-manager `trust-manager`, or distributed manually) and pass it to their Trino client as `verify=<ca-path>`.

**Files.**
- `ansible/roles/cert-manager/templates/scout-internal-issuer.yaml.j2` (new) — `ClusterIssuer` of type `selfSigned` (or a CA Issuer if Scout adopts an internal CA later). One issuer serves the cluster; trust-distribution is per-namespace via trust-manager.
- `ansible/roles/trino/templates/certificate.yaml.j2` (new) — `Certificate` resource: DNS names `trino.scout-analytics.svc.cluster.local`, `trino.scout-analytics`, `trino`. `spec.keystores.pkcs12.create=true`, `spec.keystores.pkcs12.passwordSecretRef` pointing at a `trino-keystore-password` Secret. Output Secret: `trino-tls`.
- `ansible/roles/trino/templates/values.yaml.j2` — add to `coordinator.additionalConfigProperties`:
  ```
  http-server.authentication.type=JWT
  http-server.authentication.jwt.key-file=<keycloak JWKS URL>
  http-server.authentication.jwt.required-audience=trino
  http-server.authentication.jwt.principal-field=preferred_username
  http-server.https.enabled=true
  http-server.https.port=8443
  http-server.https.keystore.path=/etc/trino/tls/keystore.p12
  http-server.https.keystore.key=${ENV:KEYSTORE_PASSWORD}
  internal-communication.shared-secret=${ENV:INTERNAL_SHARED_SECRET}
  ```
  Note: the chart's HTTP listener (port 8080) stays enabled — the chart's probes and worker internal-communication target it. JWT auth applies only to HTTPS traffic; internal-comm bypasses via the shared secret, probes hit Trino's unauthenticated `/v1/info` endpoint. HTTPS-only mode is not supported by the chart.
  Add a `secretMounts` entry mounting `trino-tls` at `/etc/trino/tls/` and an `envFrom` entry exposing the keystore password.
- `ansible/roles/trino/tasks/deploy.yaml` — apply the Certificate before the Helm install; wait for the Secret to be ready.
- `ansible/roles/cert-manager/templates/scout-ca-bundle.yaml.j2` (new) — `trust-manager` `Bundle` (if trust-manager is adopted) or a manually-generated ConfigMap that exposes the CA cert as `ca.crt` in every namespace where Trino clients run (`scout-analytics`, `scout-extractor`).
- `ansible/roles/trino/defaults/main.yaml` — new vars: `trino_keycloak_jwks_url`, `trino_keycloak_issuer`, `trino_tls_secret_name: trino-tls`, `trino_ca_configmap_name: scout-ca-bundle`.
- `ansible/roles/trino/tasks/test_auth.yaml` (new, optional) — smoke test that obtains a JWT and runs `SELECT 1` against `https://trino:8443/v1/statement` with `--cacert /etc/scout-ca/ca.crt` and `Authorization: Bearer ...`. Fold into the existing CI smoke-test harness if one exists.

**New components.** ClusterIssuer + Certificate + CA-distribution Bundle/ConfigMap. The Trino coordinator now serves on `https://trino.scout-analytics:8443` instead of `http://trino.scout-analytics:8080`.

**Validation.**
- `kubectl get secret -n scout-analytics trino-tls -o jsonpath='{.data.keystore\.p12}' | base64 -d | keytool -list -storetype PKCS12 -storepass <pwd>` shows the cert.
- `curl -k https://trino:8443/v1/info` (insecure, just to verify TLS handshake) succeeds.
- `curl --cacert /etc/scout-ca/ca.crt https://trino:8443/v1/info` returns 401 (TLS works, no auth).
- Same with a valid `client_credentials` JWT returns 200.
- `kubectl logs deploy/trino-coordinator` shows `HttpsConfiguredAuthenticator` and `JwtAuthenticator` initialized at startup.

**Rollback.** Revert the `additionalConfigProperties` block. Because this phase ships in one release with Phases 2–5, rollback rolls back the whole release.

**Dependencies.** Phase 0 (Keycloak clients exist, JWKS reachable from `scout-analytics` namespace).

**Effort.** 1–2 PRs, ~2 days. Roughly half of that is cert plumbing (Issuer, Certificate, keystore-password Secret, CA distribution). The Trino chart's config injection idiom needs verification — the chart accepts `additionalConfigProperties` on coordinator/worker; if the pinned version doesn't, a values override that drops a snippet into `coordinator.additionalConfig` is the fallback.

---

## Phase 2: OPA deployment + Rego scaffolding (allow-all baseline)

**Scope.** OPA is deployed as a 2-replica Deployment in `scout-analytics`, exposed via ClusterIP `opa.scout-analytics:8181`. Rego policy lives in `policy/` at the repo root, packaged into a bundle by CI and shipped to OPA via a Kubernetes ConfigMap mounted as a bundle source (no external bundle server — operational simplicity wins). Initial policy implements the four OPA-Trino endpoints (`/v1/data/trino/allow`, `rowFilters`, `columnMasking`, `batchColumnMasking`) with permissive defaults: `allow = true`, no filters, no masks. Trino is wired to point at OPA via `access-control.name=opa`. `delta.security` flips from `READ_ONLY` to `SYSTEM` on `trino-analytics`. Behavior: still functionally permissive (no user is denied), but every query now passes through the OPA decision path. This is the observation/load-test phase.

**Files.**
- `policy/trino/main.rego` — package `trino`, default allow `true`, `rowFilters = []`, `columnMasks = []`, helpers stubbed.
- `policy/trino/test/main_test.rego` — `opa test` cases exercising the four endpoints with sample inputs (test data lives in `policy/trino/test/fixtures/`).
- `policy/trino/keycloak.rego` — Rego module that, given a username, calls `http.send` to Keycloak's `/admin/realms/scout/users?username=<u>&exact=true` with a token obtained from `opa-keycloak-reader` (cached). Returns the user attributes dict. Use `http.send` with `cache: true, force_cache: true, cache_duration: "60s"`. The token itself is obtained via a separate cached `http.send` to Keycloak's token endpoint. Phase 2's Rego just defines these helpers; Phase 4 uses them.
- `policy/.opa-bundle.json` — bundle manifest.
- `ansible/roles/opa/` (new role): `defaults/main.yaml`, `tasks/main.yaml`, `tasks/deploy.yaml`, `templates/deployment.yaml.j2`, `templates/service.yaml.j2`, `templates/configmap.yaml.j2`, `templates/opa-config.yaml.j2`, `meta/main.yaml`. ConfigMap is rendered from `policy/` files via `ansible.builtin.fileglob`. The Deployment mounts the ConfigMap at `/policies` and runs `opa run --server --addr=0.0.0.0:8181 --config-file=/config/config.yaml /policies`. Set `readinessProbe` on `/health?ready` and `livenessProbe` on `/health`. ServiceAccount, NetworkPolicy gating ingress from Trino coordinator and egress to Keycloak.
- `ansible/site.yaml` (or equivalent playbook) — include the new `opa` role before `trino`.
- `ansible/roles/trino/templates/values.yaml.j2` — flip `delta.security=SYSTEM` on the Delta connector, and wire access control via the chart's dedicated `accessControl` top-level block (`type: properties`). The chart writes that block to `etc/access-control.properties`, which is a separate file from `etc/config.properties`; putting OPA settings into `additionalConfigProperties` lands them in the wrong file and Trino rejects them as unused config keys at startup.
  ```yaml
  accessControl:
    type: properties
    properties: |
      access-control.name=opa
      opa.policy.uri=http://opa-trino:8181/v1/data/trino/allow
      opa.policy.row-filters-uri=http://opa-trino:8181/v1/data/trino/rowFilters
      opa.policy.batch-column-masking-uri=http://opa-trino:8181/v1/data/trino/batchColumnMasks
  ```
- `ansible/roles/trino/defaults/main.yaml` — `trino_opa_endpoint: http://opa.scout-analytics:8181`.
- `.github/workflows/opa-test.yml` (new) — runs `opa test policy/` on PRs touching `policy/`.

**New components.** OPA Deployment + Service + ConfigMap + NetworkPolicy. New Ansible role. CI workflow.

**Bundle distribution choice.** ConfigMap mount (recommended): policy is small (~10 kB), the Ansible role re-renders on every play, and the OPA process picks up changes via `inotify` if `--watch` is set on `opa run` for the policy dir. Alternative considered: bundle API with an in-cluster bundle server. Rejected for operational simplicity — bundle server adds a Pod, a Service, and a credential-rotation surface for no benefit at this size.

**Validation.**
- `opa test policy/` passes locally and in CI.
- `kubectl exec -n scout-analytics deploy/opa -- curl localhost:8181/v1/data/trino/allow` returns `{"result": true}`.
- In a CI/staging deploy with OPA wired in, queries succeed (allow-all policy); Trino coordinator logs show OPA decisions logged.
- Network test: `kubectl exec -n scout-analytics deploy/opa -- curl https://keycloak...` succeeds; same `curl` from a Jupyter pod into OPA succeeds; from another namespace fails (NetworkPolicy).

**Rollback.** Revert `access-control.name=opa` and `delta.security` back to `READ_ONLY` and remove the OPA role from the playbook. Because this phase ships in one release with Phases 1, 3–5, rollback rolls back the whole release.

**Dependencies.** Phase 0 (the `opa-keycloak-reader` client must exist before OPA tries to fetch tokens).

**Effort.** 2 PRs, ~3–4 days. The Rego author needs to be careful about the OPA-Trino input shape — Trino's plugin sends a documented JSON envelope and the policy package path matters. Test data in `policy/trino/test/fixtures/` should be captured from a real Trino instance via `opa.policy.log-requests=true`.

---

## Phase 3: Per-client identity propagation (Superset, Jupyter, MCP)

**Scope.** Each client is reconfigured to authenticate to Trino as its dedicated service principal (`superset_svc`, `jupyter_svc`, `openwebui_mcp_svc`) using `client_credentials` JWTs, and to propagate the end-user identity. Behavior is still functionally permissive (OPA returns allow-all), but every query at Trino now has both a principal (the svc) and a user (the human). This phase finishes the "observe everything in audit logs" half of the rollout.

**TLS plumbing common to all clients.** Every client in this phase connects to Trino at `https://trino.scout-analytics:8443` (not the old `http://...:8080`) and mounts the Scout CA bundle ConfigMap at `/etc/scout-ca/`. Python clients pass `verify='/etc/scout-ca/ca.crt'`; the `trino-python-client` takes this via `verify=` on `trino.dbapi.connect(...)`. The MCP sidecar's outbound `aiohttp` requests pass `ssl=ssl.create_default_context(cafile='/etc/scout-ca/ca.crt')`. The per-client subsections below assume this plumbing.

### Superset

Add the `superset_svc` connection-string mode. Superset stores Trino connections as SQLAlchemy URIs; the database object needs `extra` config:
```python
{"engine_params": {"connect_args": {"auth": JWTAuthentication(token=<minted-via-keycloak>)}}}
```
But Superset's JWT must refresh — the cleanest hook is Superset's `DB_CONNECTION_MUTATOR` config that rewrites the engine on every connection open. Implement as a small `superset_trino_auth.py` config module that, on each connection, fetches a fresh access token via `client_credentials` (cached in a `flask_caching` instance keyed by client_id, refreshed at 80% of token lifetime). Also enable Superset's built-in impersonation toggle. On the Trino side, an OPA rule whitelist permits `superset_svc` to impersonate any user — policy at `policy/trino/impersonation.rego` returns `allow = true if input.action.operation == "ImpersonateUser" and input.context.identity.user in {"superset_svc", "jupyter_svc", "openwebui_mcp_svc"}`.

Files:
- `ansible/roles/superset/templates/values.yaml.j2` — extend `configOverrides` with a new `trino_jwt_auth` block, or add a new `configOverrides.trino_db_connection` section that mounts the auth helper.
- `ansible/roles/superset/files/superset_trino_auth.py` (new) — token cache + `DB_CONNECTION_MUTATOR`.
- `ansible/roles/superset/templates/values.yaml.j2` — `extraVolumes` mounting the helper.
- `ansible/roles/superset/tasks/deploy.yaml` — replace the `trino_user: '{{ trino_user }}'` line in `scout-dashboards` chart values with the new auth config (this Helm release imports dashboards, so it bakes the connection URI into Superset's DB).
- `helm/scout-dashboards/files/import.py` — update DB-creation logic to construct the JWT-authenticated SQLAlchemy URI for Trino.

### JupyterHub

`auth_state` is already enabled. The missing pieces are (a) a `pre_spawn_hook` that copies the access and refresh tokens into the spawned notebook server's environment, and (b) updating the sample notebooks and the `TRINO_USER`/auth env vars singleuser pods get.

Files:
- `ansible/roles/jupyter/templates/values.yaml.j2` — add to `hub.extraConfig` a new `auth_state_propagation` block:
  ```python
  async def pre_spawn_hook(spawner):
      auth_state = await spawner.user.get_auth_state()
      if not auth_state:
          return
      spawner.environment['KEYCLOAK_ACCESS_TOKEN'] = auth_state.get('access_token','')
      spawner.environment['KEYCLOAK_REFRESH_TOKEN'] = auth_state.get('refresh_token','')
      spawner.environment['KEYCLOAK_TOKEN_URL'] = '{{ keycloak_oidc_token_url }}'
      spawner.environment['KEYCLOAK_CLIENT_ID'] = '{{ keycloak_jupyterhub_client_id }}'
  c.Spawner.pre_spawn_hook = pre_spawn_hook
  ```
  Also remove the `TRINO_USER: '{{ trino_user }}'` line from singleuser env — notebooks will derive the user from the JWT. Add `TRINO_AUTH_MODE: 'jwt'` to signal client code.
- `helm/scout-notebook/Dockerfile` — ensure the trino-python-client version supports `JWTAuthentication` (>=0.327).
- `analytics/notebooks/**/*.py` — replace `trino.dbapi.connect(host=..., user=...)` with `trino.dbapi.connect(host=..., auth=trino.auth.JWTAuthentication(os.environ['KEYCLOAK_ACCESS_TOKEN']))`. Build a tiny `scout_trino.py` helper to encapsulate token-refresh logic (when access token expires, exchange refresh token for a new one). Sample notebooks: `quality_metrics_dashboard.py`, `rads_builder.py`, `followup_review_dashboard.py`, `cohort_builder.py`. Voila playbooks use the same env vars.
**Deferred for v1.** Voila spawns notebooks server-side from its own OAuth2 session (not via JupyterHub's `auth_state`), so it doesn't have the user's Keycloak access token available to forward. The `jupyter_svc` impersonation pattern is the right design, but it depends on Voila propagating the authenticated user's email/identity to notebook execution context — which Voila doesn't expose natively. A reasonable v1.5 implementation: Voila reads the `X-Auth-Request-Email` header set by oauth2-proxy, sets it as an env var on the spawned notebook kernel, and notebook code passes it as `X-Trino-User` while authenticating as `jupyter_svc`.

For RBAC-enabled deployments, set `enable_playbooks: false` until this lands, or accept that Voila-served notebooks run with no Trino access (the existing values.yaml.j2 still has the old shared `trino_user`, which will get rejected by JWT-enforcing Trino).

### Open WebUI MCP

**Deferred for v1.** The `mcp-trino` Helm chart configures a single fixed `trino.user` at startup and doesn't forward inbound request headers to its outbound Trino calls — so an aiohttp sidecar between Open WebUI and the MCP cannot inject per-user `X-Trino-User` (the MCP has already generated its own outbound request by the time anything could see Open WebUI's `X-OpenWebUI-User-Email`).

Two viable paths, both larger than the rest of Phase 3:
1. Fork `mcp-trino` to add header pass-through + Bearer auth (small upstream patch, possibly merge-able).
2. Run a sidecar that intercepts the MCP's outbound calls (the MCP would be configured with `trino.host=localhost:<sidecar>`, sidecar mints `openwebui_mcp_svc` JWT and forwards to real Trino). Loses per-user impersonation — all chat queries appear as `openwebui_mcp_svc`.

Until one of these lands, **the chat feature should run with `enable_chat: false`** on RBAC-enabled deployments. Users access Scout data via Superset or JupyterHub, both of which have full per-user RBAC after this release.

Files:
- `ansible/roles/trino/templates/mcp_trino_values.yaml.j2` — change `user: '{{ trino_user }}'` to `user: ''` (set per-request by the sidecar via `X-Trino-User`). Add `extraContainers` for the sidecar via post-deploy patch if the chart doesn't accept it directly.
- `ansible/roles/trino/files/mcp_auth_proxy.py` (new) — `aiohttp` proxy: token cache with refresh at 80% TTL, header rewrite, JWT injection, error mapping. Mounted into the sidecar container via a ConfigMap.
- `ansible/roles/trino/templates/mcp_auth_proxy_configmap.yaml.j2` (new) — wraps the `.py` file plus a small `requirements.txt` for the sidecar's Python image (`aiohttp`, `python-keycloak` or just `requests`).
- `ansible/roles/open-webui/templates/values.yaml.j2` — point Open WebUI's MCP URL at the sidecar (`:8081`) instead of the MCP container (`:8080`). Confirm `ENABLE_FORWARD_USER_INFO_HEADERS=true` is set so `X-OpenWebUI-User-Email` is sent.
- `ansible/roles/open-webui/templates/mcp_networkpolicy.yaml.j2` (new) — NetworkPolicy on the MCP Pod restricting ingress to the Open WebUI Pod's labels. Eliminates the header-forgery attack.
- `ansible/roles/open-webui/tasks/deploy.yaml` — apply the NetworkPolicy.

### Rego changes

- `policy/trino/impersonation.rego` (new) — allow `superset_svc`, `jupyter_svc` (unused for v1 but reserved), `openwebui_mcp_svc` to impersonate any user. Deny otherwise.

**Validation.**
- Superset: log in as a test user, run a query in SQL Lab. Trino coordinator log shows `principal=superset_svc, user=<test_user>`.
- Jupyter: spawn a notebook, run a Trino query via the helper. Trino log shows `principal=<test_user>, user=<test_user>` (direct JWT, no impersonation).
- Open WebUI: ask a SQL question, observe MCP making the call. Trino log shows `principal=openwebui_mcp_svc, user=<chat_user_email>`.
- All three paths show the human user in Trino's audit even though OPA is still allow-all.

**Rollback.** Coordinated revert with the rest of the release — there is no per-client rollback because the shared `trino` user is removed in the same release.

**Dependencies.** Phase 2 (OPA's allow-all policy must include the impersonation endpoint).

**Effort.** 3–4 PRs (one per client + Rego), ~5–7 days. The MCP sidecar is the highest-risk piece; budget extra time for it.

---

## Phase 4: Site row filter (the main RBAC payoff)

**Scope.** OPA's `rowFilters` endpoint returns a facility-scoped predicate for SELECTs on the `reports` family of tables. Logic, in order, applied identically to `scout-admin` and `scout-user` members:
- `"*"` in `allowed_facilities` → no filter.
- One or more valid facility codes → `sending_facility IN ('A', 'B')`.
- Empty/unset/invalid `allowed_facilities` → `1=0` zero-row clamp (deny-by-default).

Filters are emitted **only for tables on a configured allowlist** (`data.row_filter_tables.sending_facility`). Other tables — `information_schema.*`, `system.*`, any unscoped Delta table — get no filter, avoiding the `COLUMN_NOT_FOUND` failure that bites Superset autocomplete when a filter references a column the target table doesn't have. (This pitfall was discovered in the `trino-perms` POC; the table-scoped pattern carries forward.)

**Files.**
- `policy/trino/row_filters.rego` — implement (sketch):
  ```rego
  package trino

  facility_pattern := `^[A-Za-z0-9_-]+$`
  has_all_facilities if "*" in user_attrs.allowed_facilities

  # No group-membership gate — Trino's JWT auth already establishes that
  # the caller is an authenticated Keycloak user, and the policy is
  # attribute-driven for everyone. Users with no allowed_facilities (and
  # no "*") fall through to the 1=0 clamp below — deny by default.

  # IN-clause for users with specific facility values
  rowFilters contains {"expression": expr} if {
      not has_all_facilities
      input_table in facility_filtered_tables
      valid := [f | f := user_attrs.allowed_facilities[_]; regex.match(facility_pattern, f)]
      count(valid) > 0
      expr := sprintf("sending_facility IN (%s)",
                      [concat(",", [sprintf("'%s'", [v]) | v := valid[_]])])
  }

  # 1=0 clamp for users with no valid facility config (and no wildcard)
  rowFilters contains {"expression": "1=0"} if {
      not has_all_facilities
      input_table in facility_filtered_tables
      count([f | f := user_attrs.allowed_facilities[_]; regex.match(facility_pattern, f)]) == 0
  }
  ```
  Regex pattern validates each facility value (alphanumeric + underscore + hyphen) before interpolation — defense against a tampered Keycloak attribute injecting SQL.
- `policy/trino/keycloak.rego` — wire the actual Keycloak admin-API call (the helpers were stubbed in Phase 2).
- `policy/trino/test/row_filters_test.rego` — cases for: STL user → STL filter; multi-site user → IN-list; scout-admin → no filter; user with empty sites → 1=0; user querying a non-reports table → no filter.
- `extractor/hl7-transformer/src/hl7scout/hl7extractor/dataextraction.py` — change `add_epic_views` to emit `CREATE OR REPLACE VIEW ... SECURITY INVOKER AS ...`. Without this, row filters on the underlying `reports` table do not propagate through `reports_*_epic_view`, and admin users issuing the same view query see different data than radiologists.
- `ansible/roles/keycloak/templates/scout-realm.json.j2` — set per-client `attributes.access.token.lifespan` (seconds): `superset_svc`, `jupyter_svc`, `openwebui_mcp_svc` → `14400` (4 h, covers long-running queries). The `jupyterhub` user-OAuth client (used by Jupyter `auth_state`) stays at the realm default (300 s = 5 min); the `scout_trino.py` helper refreshes via `KEYCLOAK_REFRESH_TOKEN` between Trino submissions. Service-token lifespan trades offboarding latency for query stability — mitigated by Keycloak event-listener cache invalidation in OPA, but the service-principal token itself stays valid until expiry. Document the trade-off inline in the realm JSON.

**Validation.**
- `opa test policy/` passes the new cases.
- Test user A (`allowed_facilities=[WUSM]`) runs `SELECT COUNT(*) FROM delta.default.reports` and gets count only of WUSM rows.
- Test user B (`allowed_facilities=[BJH]`) runs the same query and gets only BJH rows.
- Scout-admin user runs the query and gets the global total.
- User with no `sites` attribute set gets 0 rows.
- Views: same behavior through `reports_curated_epic_view` after the `SECURITY INVOKER` change.
- Audit logs show the appended row filter on each query.

**Rollback.** Replace `row_filters` body with `row_filters := []`. ConfigMap reload propagates within seconds.

**Dependencies.** Phase 3 (real user identity reaches Trino) + Phase 0 (`sites` attribute populated for test users).

**Effort.** 1–2 PRs, ~3 days. Half of the work is exhaustive Rego unit tests; the other half is migrating existing views to `SECURITY INVOKER`.

---

## Phase 5: Column masks for PHI + audit tenant tag

**Scope.** Two concurrent additions:
1. **Column masks**: redact PHI-risk column contents to `'[REDACTED]'` when the user's `mask_phi_fields` attribute resolves to `true` (default for unset; set to `"false"` to grant clear-text PHI access). Same rule applies to `scout-admin` and `scout-user` members — admins who want clear text set their own `mask_phi_fields: "false"`. The masked-column allowlist lives in `data.masked_columns` and is configured in inventory — see the candidate list under "PHI fields candidate list" below. Doing this perfectly is not feasible (PHI can still leak through report-text content even when the column is masked), but the platform default moves de-identification from "SQL convention" to enforcement.
2. **Audit**: Trino event listener ships query events to Loki with a `tenant` tag derived from the user's `allowed_facilities` claim. Single facility → that facility code. Multi-valued → `multi`. Wildcard (`*`) → `all`. Empty/unset → `none` (those queries shouldn't return data anyway because of the `1=0` clamp, but capture them for diagnostics).

**Files.**
- `policy/trino/column_masks.rego` — implement batch column masks (Trino calls `/batchColumnMasks` once per query for the entire projection):
  ```rego
  should_mask if object.get(user_attrs, "mask_phi_fields", "true") != "false"

  batchColumnMasks contains {"index": i, "viewExpression": {"expression": "'[REDACTED]'"}} if {
      should_mask
      some i, resource in input.action.filterResources
      resource.column.catalogName == "delta"
      resource.column.columnName in data.masked_columns
  }
  ```
  Default for unset `mask_phi_fields` is to mask — fail-closed. Setting `mask_phi_fields: "false"` is the explicit opt-in for clear-text PHI, same opt-in path for admins and regular users.

### PHI fields masked list

For v1 the masked-column list is intentionally minimal — the goal is to prove the column-masking mechanism, not to achieve full HIPAA de-identification. Most of the schema carries research value (free-text reports for clinical content review, patient IDs for longitudinal tracking, dates for TAT and temporal analysis, diagnosis codes for cohort filtering) and stays clear-text.

Initial `trino_masked_columns`:
- `patient_name`
- `full_patient_name`
- `zip_or_postal_code`

Patient names are pure identifiers with no research use. ZIP/postal code is a HIPAA-listed indirect identifier with limited research value at the current platform scope; trivial to remove later if it proves needed.

The list is config-driven (rendered into `data.masked_columns` from the `trino_masked_columns` inventory variable), so extending the masked set is an inventory edit + Ansible run, no Rego changes.
- `policy/trino/test/column_masks_test.rego` — cases for clinical (no mask), research (NULL mask), admin (no mask), undefined persona (deny via NULL fail-closed).
- `ansible/roles/trino/templates/values.yaml.j2` — add Trino event listener configuration via `coordinator.additionalConfigProperties` with `event-listener.config-files=/etc/trino/event-listener.properties`. Mount the properties file via Helm `coordinator.additionalVolumes`.
- `ansible/roles/trino/templates/event_listener.properties.j2` (new) — `event-listener.name=http`, `http-event-listener.connect-ingest-uri=http://alloy.monitoring:3500/loki/api/v1/push`, plus a JSON formatter for query-completion events. Each event carries `principal`, `user`, query text, and the `scout_sites`/`scout_persona` claims from the JWT.

**Tenant tag derivation.** In the Trino HTTP event listener (it sees the user's JWT claims via `QueryCompletedEvent.context.user`). The listener emits a structured log line with the tenant already set. This is the simplest path because (a) Trino has the JWT in hand at audit time, (b) avoids a second OPA lookup per query for audit purposes, (c) doesn't require Alloy to learn user attributes. The Keycloak event listener (which only sees user-disable events) does not participate in tenant tagging.

**Validation.**
- Researcher persona's `SELECT mpi, report_text FROM delta.default.reports LIMIT 1` returns NULLs in those columns; non-PHI columns are populated.
- Clinical persona sees the same query with real values.
- Admin sees real values regardless of persona.
- Loki query `{app="trino"} |= "user=test_research"` shows the query event with `tenant=WUSM`.
- `opa test policy/` passes.

**Rollback.** Empty out `column_masks` rule; event listener properties file removed; chart re-applied.

**Dependencies.** Phase 4 (the row-filter machinery proves the input shape).

**Effort.** 2 PRs, ~3 days.

---

## Pre-release inventory and cleanup

Not a phase — this work is distributed across Phases 1 and 3 but worth listing in one place so nothing falls through. Before the coordinated release ships:

- `ansible/roles/scout_common/defaults/main.yaml` — remove `trino_user: trino` (Phase 1).
- `ansible/roles/extractor/templates/hl7-transformer.values.yaml.j2` — keep `TRINO_USER=trino` because it points at `trino-rw`, but rename the variable to `trino_rw_user` for clarity. The transformer is the *only* legitimate consumer of `trino-rw`; its NetworkPolicy gate (ADR 0019) is the security boundary.
- `ansible/roles/jupyter/templates/values.yaml.j2` — remove `TRINO_USER` from singleuser env (Phase 3).
- `ansible/roles/voila/templates/values.yaml.j2` — drop `TRINO_USER`, add the impersonation env vars (Phase 3).
- `ansible/roles/trino/templates/mcp_trino_values.yaml.j2` — drop `user:` line (Phase 3).
- `ansible/roles/superset/tasks/deploy.yaml` — drop `trino: { user: ... }` from `scout-dashboards` Helm values (Phase 3).
- Migration step (Ansible task or one-shot script): rewrite Superset's Trino database connection rows in Postgres to use the JWT-authenticated SQLAlchemy URI. Run as part of the Superset role's deploy task.
- `grep -r 'user="trino"' analytics/ helm/scout-notebook/` and update or remove every hit.

**Validation at end of release.**
- `kubectl exec -n scout-analytics deploy/trino-coordinator -- curl localhost:8080/v1/info` returns 401 (no token).
- Same `curl` with a bearer token from `opa-keycloak-reader` returns 200.
- All four client surfaces (Superset, Jupyter, Voila, Open WebUI) pass smoke tests with a real test user.
- Existing dashboards render for a user with the appropriate `sites` attribute.

**Rollback.** Coordinated revert of the whole release. Because the shared `trino` user is removed, the previous-version manifest must be redeployed in its entirety (no surgical revert of single files).

---

## Architectural risks to surface during implementation

- **Token audience mismatches.** Phase 0 adds the `trino-audience` client scope with an `oidc-audience-mapper` that hardcodes `aud=trino` on access tokens for the four clients that issue tokens reaching Trino: `superset_svc`, `jupyter_svc`, `openwebui_mcp_svc` (service principals), and `jupyterhub` (the user-OAuth client whose access token is pushed to notebooks via `pre_spawn_hook`). Trino's `http-server.authentication.jwt.required-audience=trino` rejects tokens lacking this audience with a 401 and no diagnostic context client-side — debugging requires Trino server logs. Decision: add the `trino-audience` scope as a `defaultClientScope` on all four clients in Phase 0, set Trino's `required-audience=trino` from day one. Alternative: accept multiple audiences (`required-audience=jupyterhub,superset_svc,...`) — fragile, grows with every new client.

- **Token TTL vs. query duration.** Trino validates JWTs at query submission (`POST /v1/statement`), not on subsequent `nextUri` polls — confirmed in Trino source (`JwtAuthenticator` only intercepts the initial request). Per-client policy:
  - **Service principals** (`superset_svc`, `jupyter_svc`, `openwebui_mcp_svc`): `accessTokenLifespan=14400` (4 h). Long enough for any reasonable dashboard or notebook session to reuse the same service token. Trade-off: a compromised service-principal token stays valid for up to 4 h; mitigated by OPA cache invalidation revoking the *user's* authority within seconds, but the service token's connection-level auth remains valid until expiry. Threat model for service-token theft is K8s namespace-RBAC, not in-cluster network exposure — handled with sealed secrets and restricted Secret RBAC.
  - **End-user JWT pass-through** (Jupyter `auth_state` via the `jupyterhub` client): realm default (300 s = 5 min). Client code in `scout_trino.py` refreshes via `KEYCLOAK_REFRESH_TOKEN` before each Trino submission. End-user offboarding is bounded by 5 min worst-case at the token layer, plus seconds-level cache invalidation at OPA.
  - Live token revocation (introspection on every request) is not used — round-trip-per-query overhead for marginal benefit over the cache invalidation path.

- **Trino view DDL needs `SECURITY INVOKER`.** The `add_epic_views` activity in `extractor/hl7-transformer/src/hl7scout/hl7extractor/dataextraction.py` must be patched to emit `CREATE OR REPLACE VIEW ... SECURITY INVOKER AS ...`. Without this, the implicit `SECURITY DEFINER` runs the underlying query as the view creator (the `trino-rw` connection's user), bypassing the row filter. Any new view created post-cutover that omits this clause silently nullifies RBAC for that view. Add a linter / unit test that grep-checks view DDL in the transformer for `SECURITY INVOKER`.

- **Existing consumers using shared `trino` user.** Inventory captured in the "Pre-release inventory and cleanup" section above. Specifically grep `analytics/` and `helm/scout-notebook/` for hardcoded `user="trino"` and rebake the singleuser image with corrections. The `hl7-transformer` keeps its access to `trino-rw` (renamed variable for clarity); everything else flips to JWT or impersonation.

- **NetworkPolicy adjustments.** Trino coordinator must reach OPA (`scout-analytics:8181`); OPA must reach Keycloak (`scout-core:8080`). The Trino Helm chart's `networkPolicy.enabled` is not currently set on `trino-analytics` (only `trino-rw` has it per `values-rw.yaml.j2`); leaving it off keeps ingress open from Superset/Jupyter/MCP pods. The OPA Deployment needs its own NetworkPolicy with explicit allow from the Trino coordinator only. The MCP NetworkPolicy in Phase 3 restricts ingress to the Open WebUI Pod by label. Trino remains intra-cluster only — **no Ingress, no Traefik route, no external exposure** — because the Trino web UI path was dropped from this design.

- **TLS cert rotation.** cert-manager rotates the Trino coordinator's cert before expiry (default 90 days, ~2/3 lifetime renewal). Trino does **not** auto-reload the keystore from disk — a pod restart is required when the Secret rotates. Wire [Stakater Reloader](https://github.com/stakater/Reloader) (or its annotation directly) on the Trino Deployment so a Secret update triggers a rolling restart; without this, the cert eventually expires and Trino starts rejecting connections. The same applies to the CA bundle ConfigMap in client pods (Superset, Jupyter, MCP) at lower frequency. Reloader is a small operator that watches annotated workloads and is worth adopting independently for this and similar TLS-rotation cases.

## Open architectural questions — recommendations

### 1. Keycloak event listener implementation

Three options:
- (a) Extend the existing `keycloak/event-listener/` Java SPI (which already implements `EventListenerProvider` for terms acceptance and approval emails). Add a new `OpaCacheInvalidationEventListenerProvider` class that listens for `USER_UPDATE`, `USER_DISABLE`, `ADMIN_EVENT(operation=UPDATE, resourceType=USER)` and POSTs to OPA. Register via the same `META-INF/services/org.keycloak.events.EventListenerProviderFactory`.
- (b) Sidecar consuming Keycloak's admin events API (Keycloak persists admin events to Postgres; sidecar polls `/admin/realms/scout/events?type=USER`).
- (c) Separate service reading Keycloak Kafka events (Keycloak Kafka event SPI is not configured today).

**Recommend (a).** The infrastructure already exists. Adding ~80 lines of Java to the package costs <1 day. The listener gets called synchronously inside Keycloak's event-dispatch transaction, so propagation latency is bounded by Keycloak's transaction commit (sub-second). The existing class already demonstrates the async-executor pattern for off-loading I/O from the event thread — reuse it for the HTTP POST to OPA. No new infra to monitor, no new credentials to rotate.

### 2. Contract between event listener and OPA cache

OPA's `http.send` cache is keyed by the full request specification (URL + method + headers + body), with no first-class invalidation API. The cleanest pattern:

- OPA's `http.send` cache for the Keycloak admin-API lookup is keyed on the username in the URL.
- The Keycloak event listener writes to OPA's data API at `PUT /v1/data/scout_cache/invalidations/<user>` with `{"invalidated_at": <epoch_ms>}` on disable/update events.
- Rego policy reads this document and computes effective cache TTL dynamically:
  ```rego
  _user_attrs(user) := attrs {
      invalidated := data.scout_cache.invalidations[user].invalidated_at
      effective_ttl := time.now_ns() - invalidated < 60 * 10^9
      attrs := http.send({"url": keycloak_url(user), "cache": true, "cache_duration": ttl_str(effective_ttl)}).body[0].attributes
  }
  ```
  By making `cache_duration` dynamic based on `_last_invalidation`, the cache is effectively busted on demand.

**Recommend:** event listener writes directly to OPA's exposed data API on disable events. Rego reads this document to compute effective cache TTL. No sidecar needed. OPA's data API is authenticated via a shared token (set in OPA's config), distributed to the event listener via a Keycloak environment variable from a K8s Secret. This contract is simple, has one moving part, and is testable in isolation.

### 3. Audit tenant tag derivation

The event listener (Keycloak) does not see queries; it sees auth events. The audit tag (`tenant`) belongs to Trino-side audit events.

**Recommend:** the Trino HTTP event listener (Phase 5) derives the tag at emission time. It has access to the user's JWT claims via `QueryCompletedEvent.context.user` and any session properties Trino has cached. If `scout_sites` is a JWT claim (which it is by Phase 0's protocol mapper), the listener reads it directly and writes the line to Loki with `tenant=<single-site-or-multi>` already set. Downstream Alloy/promtail does no extra work. This avoids:
- A second Keycloak admin lookup per query (slow).
- An OPA lookup at audit time (cycles through Rego unnecessarily).
- Alloy needing to learn user attributes from a separate source.

Cost: the Trino HTTP event listener is a small Java config file pointing at Loki's HTTP push endpoint; the JSON formatter spec is the only code surface.

## Critical files for implementation

- `ansible/roles/trino/templates/values.yaml.j2`
- `ansible/roles/keycloak/templates/scout-realm.json.j2`
- `policy/trino/main.rego`
- `ansible/roles/opa/tasks/deploy.yaml`
- `keycloak/event-listener/src/main/java/edu/washu/tag/keycloak/events/UserApprovalEmailEventListenerProvider.java` (sibling for the cache-invalidation listener)
