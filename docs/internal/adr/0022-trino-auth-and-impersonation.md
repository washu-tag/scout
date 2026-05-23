# ADR 0022: Trino Authentication and Identity Propagation

**Date**: 2026-05-19
**Status**: Accepted
**Decision Owner**: TAG Team

## Context

[ADR 0020](0020-trino-rbac-architecture.md) picks OPA as Trino's authorization engine and locks the per-user attribute model. For OPA to evaluate authorization against the actual end user, Trino has to (a) authenticate the connection cryptographically and (b) carry the real end-user identity through to the access-control plugin — not the connection's service-account identity.

In the pre-RBAC state, Trino was unauthenticated and every client connected as the hardcoded user `trino`. This ADR specifies how each client authenticates after the switch and how end-user identity reaches OPA.

The shape of the problem differs per client. Some have an active server-side session where the user's OIDC token is available (JupyterHub spawns the kernel with `auth_state`). Some have a logged-in session but no usable OIDC token (Superset's session manager handles auth, Flask sessions don't expose the IdP token to query-time hooks cleanly). Some receive the user's token from upstream as a header (Voila behind oauth2-proxy `pass_access_token`). And one — the Open WebUI MCP — comes from a third-party server (`tuannvm/mcp-trino`) whose outbound capabilities constrain what we can do.

## Requirements

1. **Cryptographic authentication on every Trino connection.** No more hardcoded shared user.
2. **End-user identity reaches the policy** (`input.context.identity.user`) so OPA can match against the right entry in `data.users`.
3. **Audit attribution.** Whatever appears in `input.context.identity.user` also appears in OPA decision logs, and via principal/user pairs in Trino's query log.
4. **Works with each client's natural session model** without bespoke per-call OIDC token refresh sitting inside user code.
5. **Air-gapped friendly.** No new control-plane services; reuse the existing Keycloak + Trino infrastructure.

## Decision

**Trino runs `http-server.authentication.type=JWT,PASSWORD` dual-auth on the HTTPS listener (port 8443). Each Trino-using client picks the most natural identity-propagation pattern for its session model: JWT pass-through for JupyterHub, custom `DB_CONNECTION_MUTATOR` + `X-Trino-User` impersonation for Superset and Voila, HTTP Basic + `X-Trino-User` impersonation (with OIDC inbound validation) for the Open WebUI MCP. Every `X-Trino-User`-using client authenticates as its own dedicated Keycloak service principal — `superset_svc`, `openwebui_mcp_svc`, `voila_svc` — with the per-principal NetworkPolicy + audit isolation that affords.**

### Architecture summary

| Layer | Choice |
|---|---|
| Authentication on Trino | `http-server.authentication.type=JWT,PASSWORD`. Client traffic on HTTPS port 8443 (TLS cert from cert-manager). The chart's HTTP listener on port 8080 stays enabled for worker↔coordinator internal-communication (authenticated via `internal-communication.shared-secret`) and Kubernetes probes (`/v1/info` is unauthenticated). HTTPS-only deployment is not supported by the Trino chart. The dual-auth mode exists so the Open WebUI MCP can connect with HTTP Basic while every other client uses JWT. |
| Service principals | One Keycloak `client_credentials`-enabled client per Trino-connecting service: `superset_svc`, `openwebui_mcp_svc`, `voila_svc`. Each has its own client secret rotated via the existing Keycloak secret pipeline. Per-principal allows per-client NetworkPolicy and per-client audit. |
| Token lifespans | Service-principal access tokens issued at 14400 s (~4 h) to cover long dashboard / notebook sessions without per-call refresh. End-user JWT pass-through (Jupyter `auth_state`) stays at the realm default and refreshes via the refresh token between submissions. |
| Audience handling | A `trino-audience` Keycloak client scope is attached to every Trino-connecting client; an `oidc-audience-mapper` injects `aud=trino` on tokens. Trino's JWT validator requires `aud=trino`. Misconfiguration produces 401s with sparse diagnostics; the realm template wires this once and forgets. |
| User mapping | Keycloak service-account users have `preferred_username = service-account-<client_id>`. Trino's `http-server.authentication.jwt.user-mapping.pattern` strips the prefix so `input.context.identity.user` is the bare `superset_svc` / `openwebui_mcp_svc` / `voila_svc` for the OPA impersonation allowlist. |
| Superset → Trino | Custom `DB_CONNECTION_MUTATOR` (`ansible/roles/superset/files/superset_trino_auth.py`) mints a `superset_svc` JWT via Keycloak `client_credentials`, attaches it as `JWTAuthentication`, and sets `X-Trino-User` to the logged-in Superset user. OPA evaluates against the impersonated user. |
| JupyterHub → Trino | JupyterHub `auth_state` exposes the user's Keycloak access token to the spawned kernel. Notebook code uses `JWTAuthentication(token)` directly — no impersonation header, the JWT *is* the user. Kernel-side helper (`ansible/roles/jupyter/files/samples/scout_trino.py`) handles refresh transparently. |
| Voila → Trino | Voila authenticates as `voila_svc` via Keycloak `client_credentials` JWT (`JWTAuthentication`, modeled on Superset's pattern). oauth2-proxy `pass_access_token` forwards the user's OIDC token to the Voila pod as `X-Auth-Request-Access-Token`; a Voila-side helper (`ansible/roles/voila/files/scout_trino.py`) extracts `preferred_username` from the header and sets `X-Trino-User` per `trino.dbapi.connect` call. NetworkPolicy restricts `voila_svc` credential consumption to the Voila pod. The Voila pod surface is pre-defined ConfigMap-mounted playbook code with no arbitrary-execution path, so the service-principal credential is bounded in what it can do. |
| Open WebUI MCP → Trino | `tuannvm/mcp-trino` v4.x authenticates outbound as `openwebui_mcp_svc` (HTTP Basic — the upstream MCP doesn't support JWT outbound). Trino's `PASSWORD` authenticator validates against `password.db` (bcrypt of the inventory secret). The MCP independently validates the inbound Keycloak OIDC token from Open WebUI, extracts `preferred_username`, and sets `X-Trino-User` per request via `TRINO_ENABLE_IMPERSONATION=true` / `TRINO_IMPERSONATION_FIELD=username`. |
| Notebook image | JupyterHub and Voila share a `scout-notebook` image based on `scipy-notebook` plus `trino-python-client`. Spark is excluded — Spark in the notebook image previously bypassed Trino by reading Delta files directly from MinIO with shared `s3a` credentials. With Spark gone, every read from the notebook image goes through Trino and is subject to the OPA policy. |
| Release model | Full coordinated release across Trino, Keycloak, and every client role. The shared `trino` user is removed in the same release that introduces JWT auth; any saved Superset connections, notebook configs, or dashboard queries that hardcoded it are migrated at the same time. No flag-gated rollout or dual-auth window. |

## Alternatives Considered

### End-user JWT pass-through from all clients

Superset, JupyterHub, the MCP, and Voila each forward the user's Keycloak token to Trino on every query. No service principal, no `X-Trino-User`, no impersonation rule in OPA.

**Rejected for Superset, Voila, and the MCP**: implementation cost vs. marginal security benefit. For Superset, a custom `DB_CONNECTION_MUTATOR` mints a `superset_svc` JWT via Keycloak `client_credentials` and sets `X-Trino-User` to the logged-in user — equivalent authorization semantics to forwarding the user's Keycloak token, without the per-user token-refresh lifecycle on long dashboard sessions or the bespoke Flask-session-introspection code path. The "insider with Kubernetes namespace access steals a service-principal credential" threat is addressed via K8s controls (sealed secrets, restricted RBAC on Secrets, rotation, NetworkPolicy on Trino), not by per-call user tokens.

For the MCP, `tuannvm/mcp-trino` v4.x only supports HTTP Basic outbound — there's no JWT-pass-through code path in the upstream MCP we can use. The shipped pattern (OIDC validation inbound + `X-Trino-User` outbound on a service-principal HTTP Basic channel) is functionally identical: the end-user identity reaches Trino, just via a header rather than as a forwarded JWT.

For Voila, the same `DB_CONNECTION_MUTATOR`-equivalent + impersonation pattern works because Voila playbooks are pre-defined ConfigMap-mounted code with no arbitrary-execution surface — there's no user-authored Python that needs to handle token refresh.

**Accepted for JupyterHub**: per-user JWT is the natural model when each notebook server is already spawned with the user's session token via `auth_state`, and the kernel runs arbitrary user code that benefits from per-call auth. No service principal, no impersonation header.

### HTTP Basic for every client (no JWT)

Use `http-server.authentication.type=PASSWORD` only; every client gets a Trino password.

**Rejected**: bcrypted passwords in `password.db` are stateful in a way Keycloak-issued JWTs aren't (rotation requires re-baking the file and bouncing Trino); they don't carry useful claims (no `aud`, no `exp` validated server-side); and they don't compose with the existing Keycloak client lifecycle. JWT is the natural fit for the three clients that can issue it; HTTP Basic is the interim accommodation for `tuannvm/mcp-trino`'s outbound limitation only.

### NetworkPolicy on the MCP to gate inbound traffic

Add a NetworkPolicy in front of `mcp-trino` allowing ingress only from the Open WebUI pod, on the theory that a network-layer gate makes the forge-`X-Trino-User`-from-another-pod attack go away.

**Rejected**: the MCP's `tuannvm/mcp-trino` v4.x already validates the inbound Keycloak OIDC token before honoring `X-Trino-User`, so requests without a valid Keycloak-signed token are rejected at the MCP. Token-forgery resistance comes from Keycloak's signing key, not from network position. A NetworkPolicy adds defense-in-depth at the cost of a coupling between the OWUI and MCP deployments; the per-token validation already covers the threat.

### Sharing one service principal across all impersonation-pattern clients

Instead of `superset_svc` + `openwebui_mcp_svc` + `voila_svc`, use one `trino_impersonator` service principal.

**Rejected**: per-principal isolation is cheap and useful. Per-client NetworkPolicy means the Voila pod compromise doesn't directly let an attacker connect as `superset_svc`. Per-client audit means OPA decision logs and Trino query logs distinguish "this came from Superset" vs "this came from the MCP." The cost is one extra Keycloak client per service, which is trivial in the realm template.

## Consequences

### Positive

- **End-user identity actually reaches the policy.** Whether via JWT (Jupyter) or impersonation header (everyone else), `input.context.identity.user` is the real user. OPA's row filters and column masks evaluate against the correct entry in `data.users`.
- **No bespoke OIDC-token refresh in user code.** Notebooks call `connect()` and get a working Trino session; the kernel-side helper handles refresh. Dashboards / playbooks do the same.
- **Per-client blast radius.** A compromised `voila_svc` credential can connect to Trino as `voila_svc` and impersonate any user — but only from the Voila pod (NetworkPolicy gate). It can't pose as Superset.
- **Audit chain works.** Trino's query log captures `principal` (the authenticated service identity) and `user` (the effective end-user identity); OPA decision logs carry the post-impersonation user. Both reach Loki via the existing log pipeline.

### Negative

- **Token-TTL-vs-query-duration is a configuration concern.** Trino validates JWTs at query submission; long-running notebook flows that span multiple submissions can outlive Keycloak's default 5-minute access-token TTL. Service-principal tokens are issued at 4 h to cover query duration; end-user JWT pass-through (Jupyter `auth_state`) refreshes via the refresh token between submissions.
- **PASSWORD authenticator is an extra surface for one client only.** `password.db` exists solely because the upstream MCP doesn't support JWT outbound. If/when a custom MCP that supports JWT lands, the `PASSWORD` authenticator + `password.db` + `openwebui_mcp_svc` password come out.
- **Full-reinstall release.** RBAC ships as a single coordinated update; the shared `trino` user is removed in the same release. Any saved Superset connections, notebook configs, or dashboard queries that hardcoded `trino` are migrated at the same time, not at a separate cutover moment.
- **Token audience handling is a recurring gotcha.** Keycloak issues tokens with `aud=<client>` by default; misconfiguration of the `trino-audience` scope produces 401s with limited diagnostic information. The realm template gets this right but anyone touching new Trino-connecting clients has to remember to attach the scope.

## Implementation Notes

- **Trino TLS**: cert-manager-issued PKCS12 keystore mounted into the coordinator; Trino listens on `https://trino.scout-analytics:8443`. Internal CA bundle distributed to client namespaces (Superset, Jupyter, MCP, Voila) via a Secret/ConfigMap that clients mount and pass to their HTTP libraries as `verify=<ca-path>`. JWT auth requires TLS per Trino's auth model.
- **Trino impersonation allowlist** (`policy/trino/main.rego`, `trino_service_principals` set): `superset_svc`, `openwebui_mcp_svc`, `voila_svc`. No wildcard. The same set drives both the `ImpersonateUser` allow rule and the `is_system_identity` carve-out that exempts service principals from the `user_enabled` gate (they aren't in `data.users`).
- **Keycloak realm template** (`ansible/roles/keycloak/templates/scout-realm.json.j2`) renders the three service-principal clients with `serviceAccountsEnabled: true`, the `trino-audience` scope attached, and `access.token.lifespan = 14400`. The corresponding secrets live in inventory under `keycloak_<name>_svc_client_secret`.
- **Voila kernel env propagation**: `oauth2-proxy` is configured with `set_xauthrequest = true` + `pass_access_token = true`, so the Traefik `oauth2-proxy-auth` middleware (`authResponseHeaders: [X-Auth-Request-Access-Token]`) carries the user's OIDC token onto every request reaching Voila. `scout_voila.py` monkey-patches Voila's Tornado handler to capture the header into a contextvar; a `ScoutMappingKernelManager` subclass (registered via `c.VoilaConfiguration.multi_kernel_manager_class` in `/etc/jupyter/voila.py`) propagates the token into the spawned kernel's env as `X_AUTH_REQUEST_ACCESS_TOKEN`. `scout_trino.connect()` in the kernel decodes `preferred_username` from the env-var token and sets it as the impersonation header.
- **MCP inbound validation**: the MCP's `oauth.enabled = true` + `oauth.mode = native` config validates the Keycloak OIDC token on every inbound request; `TRINO_IMPERSONATION_FIELD = username` picks `preferred_username` from the validated token's claims and sets it as `X-Trino-User` on the outbound Trino call.
- **Trino-rw is not in scope.** The write-enabled `trino-rw` instance has no OPA, no JWT auth, no impersonation — it's gated by NetworkPolicy per ADR 0019 and accessed anonymously by the specific pods on its allowlist (hl7-transformer for view DDL, Voila for reviewer-annotation writeback).

## Future Considerations

- **Custom MCP supporting JWT outbound** — drops the `PASSWORD` authenticator and the `password.db` interim. Target timeline tied to in-house MCP development.
- **Trino event-listener tenant-tagged audit stream** — emit a Loki stream with a `tenant` label derived from the user's `allowed_facilities` attribute (single facility → that facility code; multi-valued → `multi`; wildcard → `all`; empty → `none`). OPA decision logs cover per-query attribution today; this would add a Trino-side audit channel.
- **JupyterHub kernel-side scout_trino as an installed package** rather than a samples copy — would let the helper version-pin and ship updates independently of the singleuser image rebuild.

## References

- ADR 0003: OAuth2 Proxy as Authentication Middleware — UI-layer auth that Voila's `X-Auth-Request-Access-Token` forwarding builds on.
- ADR 0019: Read-Write Trino Instance for Transformer-Issued View DDL — explains why `trino-rw` is gated by NetworkPolicy rather than this ADR's auth model.
- ADR 0020: Trino Authorization via OPA with Keycloak Attributes — the policy engine whose `input.context.identity.user` this ADR delivers.
- ADR 0021: OPA User Attribute Distribution via MinIO Bundles — the data side of how OPA knows about users.
- ADR 0023: Trino View Security Model — the policy carve-outs that affect how DEFINER views interact with the impersonated identity.
- Trino docs: JWT authentication — <https://trino.io/docs/current/security/jwt.html>
- Trino docs: Password file authentication — <https://trino.io/docs/current/security/password-file.html>
