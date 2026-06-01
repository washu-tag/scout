# ADR 0022: Trino Authentication and Identity Propagation

**Date**: 2026-05-19
**Status**: Accepted
**Decision Owner**: TAG Team

## Context

[ADR 0020](0020-trino-authz-architecture.md) picks OPA as Trino's authorization engine and locks the per-user attribute model. For OPA to evaluate authorization against the actual end user, Trino has to:

1. Authenticate the connection cryptographically (no more hardcoded shared user).
2. Carry the real end-user identity through to the access-control plugin — not the connection's service-account identity.

Trino was previously unauthenticated; every client connected as the hardcoded user `trino`. This ADR specifies how each Trino-using client authenticates and how end-user identity reaches `input.context.identity.user` so OPA can index into `data.users`.

The shape of the problem differs per client:

- **JupyterHub** has the user's OIDC token available server-side (via `auth_state`) and the kernel runs arbitrary user code — natural fit for per-call user JWT.
- **Superset** has a logged-in user but no clean way to expose the IdP token at query time (Flask sessions don't surface it to the connector hook).
- **Voila** receives the user's OIDC token from upstream as an oauth2-proxy-injected header, but Voila itself has no native "pass session token to the kernel" mechanism.
- **Open WebUI MCP** runs a third-party server (`tuannvm/mcp-trino`) whose outbound capabilities are HTTP-Basic-only — no JWT outbound code path.

## Requirements

1. **Cryptographic authentication** on every Trino connection.
2. **End-user identity reaches the policy** so OPA matches the right entry in `data.users`.
3. **Audit attribution.** The user that appears in `input.context.identity.user` also appears in OPA decision logs and Trino's query log.
4. **No bespoke OIDC-token refresh in user code.** Notebooks / playbooks / dashboards call `connect()` and get a working Trino session.
5. **Air-gapped friendly.** No new control-plane services; reuse the existing Keycloak + Trino infrastructure.

## Decision

**Trino runs dual-auth (`PASSWORD,JWT`) on an HTTPS listener. Each client picks one of three identity-propagation patterns determined by its session model:**

| Pattern | Outbound to Trino | Who appears in `identity.user` | Used by |
|---|---|---|---|
| **JWT pass-through** | User's own Keycloak JWT | The end user (from JWT's `preferred_username`) | JupyterHub |
| **JWT + impersonation** | Service-principal JWT (`client_credentials`) + `X-Trino-User: <end user>` | The end user (from the header) | Superset, Voila |
| **HTTP Basic + impersonation** | Service-principal password + `X-Trino-User: <end user>` | The end user (from the header) | Open WebUI MCP |

Each impersonation-pattern client gets its own Keycloak service principal — `superset_svc`, `voila_svc`, `openwebui_mcp_svc` — so a compromised credential blasts only that client's surface.

### Trino-side configuration

| Config | Value | Why |
|---|---|---|
| Listener | HTTPS on **8443** (TLS via cert-manager) for clients; HTTP on **8080** for worker↔coordinator (`internal-communication.shared-secret`) and Kubernetes probes | The trinodb/trino chart hard-codes the dual listener; HTTPS-only isn't a supported chart mode. JWT auth requires TLS. |
| `http-server.authentication.type` | `PASSWORD,JWT` | PASSWORD validates the MCP's HTTP-Basic credential against `password.db` (bcrypt of `keycloak_openwebui_mcp_svc_client_secret`). JWT validates everything else via Keycloak's JWKS. |
| `jwt.principal-field` | `preferred_username` | For service principals, Keycloak sets this to `service-account-<client-id>`. For end-user tokens, it's the user's Keycloak username. |
| `jwt.user-mapping.pattern` | `(?:service-account-)?(.+)` | Keycloak auto-names every service-account user `service-account-<clientId>` (not our choice); the optional-prefix strip normalizes that to the bare `<clientId>` while leaving end-user usernames untouched, so one `principal-field` serves both token shapes. `service-account-superset_svc` → `superset_svc`; `alice` → `alice`. |
| `jwt.required-audience` | `trino` | Keycloak's `trino-audience` client scope (attached to every Trino-connecting client) injects `aud=trino`. Without the scope, Keycloak issues `aud=<client>` and Trino rejects with a sparse 401. |

### Per-client flow

```
JupyterHub (JWT pass-through)
─────────────────────────────
  user logs in via Keycloak OIDC → Hub stores auth_state {access, refresh} Hub-side
       ↓ (kernel holds only JUPYTERHUB_API_TOKEN — no Keycloak token at spawn)
  notebook → scout_trino fetches the ACCESS token from the Hub API on demand
    (Hub's refresh_user refreshes it near expiry; refresh_token + client_secret never reach the pod)
       ↓
  notebook code: trino.dbapi.connect(auth=JWTAuthentication(access_token))   # token: aud=trino
       ↓
  Trino: principal=alice, user=alice → OPA evaluates against data.users["alice"]
                                       impersonation: none (the JWT subject IS the user)

Superset (JWT + impersonation)
──────────────────────────────
  user logs in → Superset Flask session
       ↓
  SQL Lab query → DB_CONNECTION_MUTATOR fires
       ↓
  mutator mints superset_svc JWT (client_credentials; token: aud=trino; cached, re-minted before exp)
       ↓
  HTTP: Authorization: Bearer <superset_svc JWT>, X-Trino-User: alice
       ↓
  Trino: principal=superset_svc, user=alice → OPA evaluates against data.users["alice"]
                                       impersonation: superset_svc ∈ trino_service_principals

Voila (JWT + impersonation, with a header chain)
────────────────────────────────────────────────
  user → oauth2-proxy (pass_access_token=true, set_xauthrequest=true)
       ↓ X-Auth-Request-Access-Token: <user OIDC token>
  Traefik forwardAuth → Voila pod
       ↓ scout_voila overrides Voila's GET handler → stashes token in a contextvar
  Voila spawns kernel → ScoutMappingKernelManager (class config) reads contextvar → kernel env
       ↓
  playbook code: scout_trino.connect()
                   - extracts preferred_username from the captured user token
                     (used as a username source only — not sent to Trino, not
                     validated server-side; revocation enforced via OPA bundle)
                   - mints voila_svc JWT (client_credentials; token: aud=trino; re-minted before exp)
                   - HTTP: Bearer <voila_svc JWT>, X-Trino-User: <user>
       ↓
  Trino: principal=voila_svc, user=alice → OPA evaluates against data.users["alice"]
                                       impersonation: voila_svc ∈ trino_service_principals

Open WebUI MCP (HTTP Basic + impersonation)
───────────────────────────────────────────
  user chats in OWUI → OWUI forwards user's OIDC token to MCP
       ↓
  mcp-trino: oauth.enabled=true validates inbound token vs Keycloak   # inbound token: aud=mcp-trino
       ↓ TRINO_IMPERSONATION_FIELD=username → reads preferred_username from validated token
  outbound to Trino: Authorization: Basic <openwebui_mcp_svc:password>, X-Trino-User: <user>
       ↓ (HTTP Basic, not a JWT — Trino's aud=trino check doesn't apply to this leg)
  Trino: principal=openwebui_mcp_svc, user=alice → OPA evaluates against data.users["alice"]
                                       impersonation: openwebui_mcp_svc ∈ trino_service_principals
```

### Token lifespans

| Token type | Lifespan | Refresh path |
|---|---|---|
| Service-principal access token (`superset_svc`, `voila_svc`) | 14400 s (~4 h) | Client helpers re-mint before exp via cached `client_credentials` |
| MCP service-principal password | Static (rotated via Ansible) | N/A — bcrypted into `password.db` |
| End-user access token (Jupyter, held Hub-side in `auth_state`) | Realm default (~5 min access, ~30 min refresh) | Hub's `refresh_user` refreshes via the refresh token; `scout_trino` re-fetches from the Hub API before expiry |

Service-principal tokens are deliberately long-lived (~4 h) so token minting stays off the per-query hot path — the client helpers still re-mint them before expiry (see the table above), just on an hours cadence rather than per request. They never leave the pod that owns them (Kubernetes Secret + NetworkPolicy on Trino).

### Impersonation gate

The OPA policy gates who can set `X-Trino-User`:

```rego
trino_service_principals := {"superset_svc", "openwebui_mcp_svc", "voila_svc"}

allow if {
    input.action.operation == "ImpersonateUser"
    input.context.identity.user in trino_service_principals
}
```

A principal is recognized as a service principal purely by membership in this hardcoded set — no attribute or group drives it. Each name is the bare client ID of a Keycloak `serviceAccountsEnabled` client: Keycloak names its service-account user `service-account-<clientId>`, and Trino's `jwt.user-mapping.pattern` strips the prefix so `identity.user` is `<clientId>`. The set is hardcoded for the same reason as `approved_groups` (ADR 0020) — the rego and the realm template are owned by the same team, so adding a principal is one rego edit plus one client.

A regular user (JupyterHub JWT pass-through) attempting `X-Trino-User: bob` to escalate would fail this rule — their principal is `alice`, not a service principal. Combined with Keycloak's signing key (the only way to mint a JWT that survives JWKS validation), the only way to forge identity is to compromise a service-principal credential.

### What `trino` (the legacy shared user) is now

The `trino` user no longer exists as a human-facing connection (saved Superset connections, notebook hardcoded configs all migrated in the same release). It survives only as the `hl7-transformer`'s identity against `trino-rw` (NetworkPolicy-gated, no OPA — see [ADR 0019](0019-trino-rw-for-views.md)) and as the DEFINER view owner per [ADR 0023](0023-trino-view-security-model.md). End users never connect as `trino`.

## Alternatives Considered

### End-user JWT pass-through from all clients

Superset, JupyterHub, the MCP, and Voila each forward the user's Keycloak token to Trino on every query. No service principal, no `X-Trino-User`, no impersonation rule in OPA.

**Rejected for Superset, Voila, and the MCP**: implementation cost vs. marginal security benefit. For Superset, a custom `DB_CONNECTION_MUTATOR` mints a `superset_svc` JWT and sets `X-Trino-User` to the logged-in user — equivalent authorization semantics to forwarding the user's Keycloak token, without per-user token-refresh lifecycle on long dashboard sessions or bespoke Flask-session-introspection. The "insider with namespace access steals a service-principal credential" threat is addressed by K8s controls (Kubernetes RBAC on the credential Secret, rotation, NetworkPolicy on Trino), not per-call user tokens.

For the MCP, `tuannvm/mcp-trino` v4.x only supports HTTP Basic outbound — there's no JWT-pass-through code path in the upstream MCP. The shipped pattern (OIDC validation inbound + `X-Trino-User` outbound on a service-principal HTTP Basic channel) is functionally identical: the end-user identity reaches Trino, just via a header rather than as a forwarded JWT.

For Voila, the `DB_CONNECTION_MUTATOR`-equivalent + impersonation pattern works because Voila playbooks are pre-defined ConfigMap-mounted code with no arbitrary-execution surface.

**Accepted for JupyterHub**: per-user JWT is the natural model when each notebook server is already spawned with the user's session token via `auth_state`, and the kernel runs arbitrary user code that benefits from per-call auth.

### HTTP Basic for every client (no JWT)

Use `http-server.authentication.type=PASSWORD` only; every client gets a Trino password.

**Rejected**: bcrypted passwords in `password.db` are stateful in a way Keycloak-issued JWTs aren't (rotation requires re-baking the file and bouncing Trino); they don't carry useful claims (no `aud`, no `exp` validated server-side); and they don't compose with the existing Keycloak client lifecycle. JWT is the natural fit for the three clients that can issue it; HTTP Basic is the interim accommodation for `tuannvm/mcp-trino`'s outbound limitation only.

### NetworkPolicy on the MCP to gate inbound traffic

Add a NetworkPolicy in front of `mcp-trino` allowing ingress only from the Open WebUI pod, on the theory that a network-layer gate makes forge-`X-Trino-User`-from-another-pod attacks go away.

**Rejected**: the MCP already validates the inbound Keycloak OIDC token before honoring `X-Trino-User`, so requests without a valid Keycloak-signed token are rejected at the MCP. Token-forgery resistance comes from Keycloak's signing key, not from network position.

### Sharing one service principal across all impersonation-pattern clients

Instead of `superset_svc` + `openwebui_mcp_svc` + `voila_svc`, use one `trino_impersonator` service principal.

**Rejected**: per-principal isolation is cheap and useful. A Voila pod compromise doesn't directly let an attacker connect as `superset_svc`; per-client audit means OPA decision logs and Trino query logs distinguish "this came from Superset" vs "this came from the MCP." The cost is one extra Keycloak client per service.

## Consequences

### Security boundary

| Threat | Mitigation |
|---|---|
| User forges `X-Trino-User` to read another user's data | OPA `ImpersonateUser` rule restricts impersonation to service principals; users can't authenticate as one. |
| Stolen end-user JWT | JWT expires (~5 min); refresh requires Keycloak session. Stolen JWT is bounded in time and traceable in OPA decision logs. |
| Stolen service-principal credential | A leaked credential lets the holder authenticate as that principal and impersonate any user, so the controls aim to prevent and contain leaks: a NetworkPolicy limits which pods can reach Trino, and Kubernetes RBAC on the credential Secret limits which pods can read it. Per-principal credentials (not one shared impersonator) keep a compromise scoped to that single client. |
| MCP container compromise | Equivalent to service-principal compromise — attacker can connect as `openwebui_mcp_svc` and impersonate any user. NetworkPolicy on Trino + Kubernetes RBAC on the credential Secret are the gates. |
| Decision-log tampering | OPA decision logs ship to Loki via the existing log pipeline; Trino's query log captures `(principal, user)` pairs. Both stores are append-only from Trino/OPA's perspective. |

### Positive

- **End-user identity reaches the policy.** Whether via JWT (Jupyter) or impersonation header (everyone else), `input.context.identity.user` is the real user. OPA's row filters and column masks evaluate against the correct entry in `data.users`.
- **No bespoke OIDC-token refresh in user code.** Notebooks call `connect()` and get a working Trino session; kernel-side helpers handle refresh. Dashboards / playbooks do the same.
- **Per-client blast radius.** A compromised `voila_svc` credential connects from the Voila pod only (NetworkPolicy) and identifies as `voila_svc` in audit logs.
- **Audit chain works.** Trino's query log captures `(principal, user)` pairs; OPA decision logs carry the post-impersonation user. Both reach Loki.

### Negative

- **Token-TTL-vs-query-duration is a configuration concern.** Trino validates JWTs at query submission; long-running notebook flows can outlive Keycloak's default 5-minute access-token TTL. Service-principal tokens are issued at 4 h to cover query duration; end-user JWT pass-through (Jupyter `auth_state`) refreshes via the refresh token between submissions.
- **PASSWORD authenticator is extra surface for one client only, and likely interim.** `password.db` exists solely because `tuannvm/mcp-trino` doesn't support JWT outbound. The third-party MCP is likely to be replaced with a bespoke OWUI tool that sends a JWT to Trino the same way Superset and Voila do — when that lands, the `PASSWORD` authenticator, `password.db`, and the `openwebui_mcp_svc` static password all go away.
- **Full-reinstall release.** AuthZ ships as a single coordinated update; the shared `trino` user is removed from human-facing surfaces in the same release. Saved Superset connections, notebook configs, and dashboards that hardcoded it are migrated at the same time.
- **Token audience handling is a recurring gotcha.** Keycloak issues tokens with `aud=<client>` by default; misconfiguration of the `trino-audience` scope produces 401s with limited diagnostic information. Anyone adding a new Trino-connecting client has to remember to attach the scope.

## Implementation Notes

- **Trino TLS**: cert-manager-issued PKCS12 keystore mounted into the coordinator. Internal CA bundle distributed to client namespaces via a Secret/ConfigMap; clients pass it as `verify=<ca-path>` to their HTTP libraries.
- **Impersonation allowlist**: `trino_service_principals` in `policy/trino/main.rego` — the same set drives the `ImpersonateUser` allow rule and the `is_system_identity` carve-out from `user_enabled` (service principals aren't in `data.users`).
- **Keycloak realm template** (`ansible/roles/keycloak/templates/scout-realm.json.j2`) renders the three service-principal clients with `serviceAccountsEnabled: true`, the `trino-audience` scope attached, and `access.token.lifespan = 14400`. Secrets live in inventory under `keycloak_<name>_svc_client_secret`.
- **Voila kernel env propagation**: the kernel manager is swapped via config (`VoilaConfiguration.multi_kernel_manager_class`), but Voila exposes no equivalent hook for its GET handler, so `scout_voila` overrides the handler directly to stash the forwarded token in a contextvar the manager reads at spawn. Contained to one method; the no-header path logs and falls back to anonymous (zero rows). The captured user token's only purpose inside the kernel is to extract the `preferred_username` claim — it is never sent to Trino, never validated server-side at query time, and never used as an auth credential. Revocation is enforced dynamically via the OPA bundle (5–15s propagation per ADR 0021), so a kernel holding a stale user token can't extend a revoked user's access.
- **MCP inbound validation**: the MCP's `oauth.enabled = true` + `oauth.mode = native` config validates the Keycloak OIDC token on every inbound request; `TRINO_IMPERSONATION_FIELD = username` picks `preferred_username` and sets it as `X-Trino-User`.
- **`trino-rw` is not in scope.** The write-enabled instance has no OPA, no JWT auth, no impersonation — gated by NetworkPolicy per ADR 0019 and accessed by the specific pods on its allowlist (`hl7-transformer` for view DDL, Voila for reviewer-annotation writeback).

## Future Considerations

- **Bespoke OWUI tool replacing `tuannvm/mcp-trino`** — in-house OWUI tool sends a JWT to Trino directly, matching the JWT-with-impersonation pattern used by Superset and Voila. Drops the `PASSWORD` authenticator, `password.db`, and the `openwebui_mcp_svc` static password — all interim accommodations for the third-party MCP's HTTP-Basic-only outbound.
- **Trino event-listener tenant-tagged audit stream** — emit a Loki stream with a `tenant` label derived from the user's `allowed_facilities` attribute (single facility → that facility code; multi-valued → `multi`; wildcard → `all`; empty → `none`). OPA decision logs cover per-query attribution today; this would add a Trino-side audit channel.
- **JupyterHub kernel-side `scout_trino` as an installed package** rather than a samples copy — version-pinned, ships updates independently of singleuser image rebuilds.

## References

- ADR 0003: OAuth2 Proxy as Authentication Middleware — UI-layer auth that Voila's `X-Auth-Request-Access-Token` forwarding builds on.
- ADR 0019: Read-Write Trino Instance for Transformer-Issued View DDL — explains why `trino-rw` is gated by NetworkPolicy rather than this ADR's auth model.
- ADR 0020: Trino Authorization via OPA with Keycloak Attributes — the policy engine whose `input.context.identity.user` this ADR delivers.
- ADR 0021: OPA User Attribute Distribution via MinIO Bundles — the data side of how OPA knows about users.
- ADR 0023: Trino View Security Model — the policy carve-outs that affect how DEFINER views interact with the impersonated identity.
- Trino docs: JWT authentication — <https://trino.io/docs/current/security/jwt.html>
- Trino docs: Password file authentication — <https://trino.io/docs/current/security/password-file.html>
