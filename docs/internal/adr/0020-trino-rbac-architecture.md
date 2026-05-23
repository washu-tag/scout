# ADR 0020: Trino RBAC via OPA with Keycloak Attributes

**Date**: 2026-05-19
**Status**: Accepted
**Decision Owner**: TAG Team

## Context

Scout's user-facing Trino instance has **no authentication and no authorization** in the pre-RBAC state. Every caller — Superset, JupyterHub, Open WebUI's MCP tool, ad-hoc `kubectl exec` users — connects as the hardcoded user `trino`. Read-only behavior is enforced at the connector layer (`delta.security=READ_ONLY`), at the metastore (read-only PostgreSQL role on `hive_metastore_endpoint_readonly`), and at MinIO (`s3_lake_reader` bucket policy). These prevent writes; they do not constrain *what data* a given user can read. Every user sees every row of every report from every facility.

This was acceptable while Scout was a single-tenant deployment whose only access control was cluster-network membership. It is not acceptable for the platform's direction: a multi-tenant analytics layer over HL7 radiology reports from multiple partner institutions, each of which must see only their own data and which may further partition access by clinical vs. research persona, by modality sub-specialty, and (eventually) by cohort/IRB project.

### Requirements driving this ADR

1. **Per-user row-level access** to the `reports` family of tables, scoped by `sending_facility` at minimum.
2. **PHI column masking.** Doing this to perfection is probably not feasible, but redacting contents of certain columns reduces risk. E.g., redact report text columns, non-Scout patient ids, dates for users without the appropriate permission.
3. **Real-time onboarding and offboarding.** A user disabled at their partner institution must lose access within seconds, not at the next sync interval. Stated security requirement.
4. **Multi-client identity.** Superset, JupyterHub, Voila, and Open WebUI's Trino MCP must each propagate the actual end-user identity to Trino rather than connecting as a shared service principal.
5. **No combinatorial group sprawl in Keycloak.** Modeling N sites × M modalities × K personas as N×M×K Keycloak groups is operationally untenable.
6. **GitOps-friendly policy.** Policy changes are reviewable in pull requests and tested in CI, like the rest of the codebase.

### What this ADR does *not* cover

- **Storage-layer multitenancy** (per-site Delta schemas, per-tenant buckets) is independent of Trino RBAC and a future ADR; this one is solely about query-time RBAC.
- **The write-enabled `trino-rw` instance** stays gated by NetworkPolicy per ADR 0019. RBAC applies only to the user-facing Trino in `scout-analytics`.
- **Cohort/project-based access** (membership lists managed by PIs) is deferred until the use case materializes. The architecture chosen here supports adding it later via a Trino ACL table without rework, but we don't even have a cohort entity today.

## Decision

**Trino enforces RBAC via Open Policy Agent (OPA). User identity is established via Keycloak-issued JWT (programmatic clients). Authorization attributes are stored on the user object in Keycloak and distributed to OPA via a Keycloak SPI listener that publishes an OPA bundle to MinIO — OPA reads `data.users[user]` directly at decision time, with no per-decision Keycloak call (see [ADR 0021](0021-opa-user-attribute-distribution.md) for the distribution mechanism). Per-client identity propagation uses the impersonation pattern (Superset, Open WebUI MCP, Voila) or JWT pass-through (JupyterHub), depending on which mechanism each client supports natively.**

### Architecture summary

| Layer | Choice |
|---|---|
| Authentication | `http-server.authentication.type=JWT` — client traffic on HTTPS port 8443 (TLS cert from cert-manager). The chart's HTTP listener on port 8080 remains enabled for worker↔coordinator internal-communication (authenticated via `internal-communication.shared-secret`, no JWT) and Kubernetes probes (`/v1/info` is unauthenticated). HTTPS-only deployment is not supported by the Trino chart. Trino web UI not exposed externally. |
| Authorization | `access-control.name=opa`; `delta.security=READ_ONLY` retained at the connector as defense in depth. OPA is the primary gate; the connector-level read-only setting backstops it. Combined with the metastore-level read-only PostgreSQL role (ADR 0019) and MinIO `s3_lake_reader` credentials, writes are blocked at three layers. |
| Identity model | Existing `scout-admin` and `scout-user` Keycloak groups continue to gate admin/user permissions in other Scout apps; OPA's Trino policy itself is **purely attribute-driven** (no group check). Per-user attributes are Keycloak User Profile entries stored as `Map<String, List<String>>`: `allowed_facilities` (multivalued, supports `*` wildcard) for row scoping; `mask_phi_fields` (multivalued, defaults to mask; `["false"]` opts out) for column masking. Admins set their own attributes to test restricted views; opt into unrestricted access via `allowed_facilities: ["*"]` + `mask_phi_fields: ["false"]`. |
| Attribute distribution | Keycloak SPI listener publishes user attributes as an OPA bundle (`scout/bundle.tar.gz`) to MinIO; OPA's native bundle plugin pulls every 5–10 s and atomically swaps the `data.users` subtree. Decision-time policy reads `data.users[user]` directly — no `http.send`, no admin-API client. See [ADR 0021](0021-opa-user-attribute-distribution.md). |
| Real-time invalidation | Same mechanism as above — the bundle is republished on every admin USER UPDATE / CREATE / DELETE event (debounced to coalesce bursts); steady-state staleness floor is the bundle pull interval (5–10 s). |
| Policy language | Rego, in `policy/` at the repo root; `opa test` in CI. |
| OPA topology | 2-replica Deployment behind ClusterIP in `scout-analytics` namespace. |
| Superset → Trino | Custom `DB_CONNECTION_MUTATOR` (`ansible/roles/superset/files/superset_trino_auth.py`) mints a `superset_svc` JWT via Keycloak client_credentials, attaches it as `JWTAuthentication`, and sets `X-Trino-User` to the logged-in Superset user. OPA evaluates against the impersonated user. |
| JupyterHub → Trino | JupyterHub `auth_state` exposes Keycloak access token to spawned notebooks; clients use `JWTAuthentication`. |
| Voila → Trino | Voila authenticates as `voila_svc` via Keycloak `client_credentials` JWT (`JWTAuthentication`, modeled on Superset's pattern) and impersonates the session user via `X-Trino-User`. oauth2-proxy `pass_access_token` forwards the user's OIDC token to the Voila pod; a Voila-side helper (`ansible/roles/voila/files/scout_trino.py`) extracts `preferred_username` and sets `X-Trino-User` per `trino.dbapi.connect` call. NetworkPolicy restricts `voila_svc` credential consumption to the Voila pod. |
| Open WebUI MCP → Trino | MCP authenticates as `openwebui_mcp_svc` (HTTP Basic), validates the inbound Keycloak OIDC token from Open WebUI, extracts `preferred_username`, and sets `X-Trino-User` per request via `TRINO_ENABLE_IMPERSONATION=true` / `TRINO_IMPERSONATION_FIELD=username`. Trino runs in `PASSWORD,JWT` dual-auth mode to accommodate `tuannvm/mcp-trino` v4.x's HTTP Basic-only outbound. |
| Audit | OPA decision logs (emitted by every access-control evaluation) carry the post-impersonation user and are shipped to Loki via the existing log pipeline. A Trino-side event listener emitting a `tenant`-tagged audit stream is a future enhancement; OPA's decision logs cover the per-query-attribution requirement today. |
| Release model | Full coordinated release across all client roles; no flag-gated rollout or dual-auth window. The shared `trino` user is removed in the same release that introduces JWT auth. |

## Alternatives Considered

### File-based system access control (`access-control.name=file`)

JSON rules file with row filters and column masks. No new runtime; well-documented.

**Rejected**: scales poorly for the policy complexity we need. The expressions allowed in `filter` and `mask` fields are single SQL expressions per rule — fine for simple cases but awkward for conditional masking (clinical-vs-research persona logic), unwieldy for cohort-style subqueries, and untestable as a unit. The deeper problem is that the attribute model lives in Keycloak; file rules require those attributes to be materialized into Trino's group provider, which means either (a) a custom group provider plugin, or (b) a cronjob materializing a `groups.txt` file — and file-from-cron is insufficient for real-time offboarding. OPA solves this without a custom Trino plugin: a Keycloak SPI listener publishes user attributes as an OPA bundle to MinIO, OPA pulls it natively (ADR 0021), and the rego reads `data.users[user]` at decision time.

### Apache Ranger

Centralized policy server with web UI, row filters, column masks, built-in audit. Available as a Trino plugin (in-tree since Trino 466).

**Rejected**: operational footprint disproportionate to the deployment. Ranger requires a Ranger Admin webapp, a relational DB for policy storage, Solr or Elasticsearch for audit, and typically UserSync against LDAP/AD. For a single-engine deployment (Trino only) maintained by a small team using GitOps for everything else, the infrastructure burden, the UI-as-source-of-truth model, and the limited policy testing story dominate the benefits. Reconsider only if Scout adds direct Hive/Spark/Kafka authz needs or grows a security team that authors policy outside engineering.

### SQL standard access control (`hive.security=sql-standard`, `GRANT`/`REVOKE`)

In-band `GRANT SELECT ON ... TO ROLE radiologists` syntax stored in metastore ACL tables.

**Rejected**: the Delta Lake connector does not accept `delta.security=sql-standard` — supported values are `ALLOW_ALL`, `SYSTEM`, `READ_ONLY`, `FILE`. Even sharing the Hive metastore, Delta tables do not inherit Hive's GRANT/REVOKE semantics. Not an option for Scout's silver layer.

### Custom Trino `GroupProvider` plugin

JVM plugin that, given a username, fetches groups from Keycloak's admin API in real time. Pairs with file-based or OPA authz.

**Rejected**: the work the plugin would do — surface per-user attributes to Trino — is delivered without a Trino plugin by the ADR 0021 bundle pipeline (Keycloak SPI → MinIO → OPA). Building, packaging, and maintaining a Java plugin (with its own breaking-change exposure to the Trino SystemAccessControl/GroupProvider SPIs) adds engineering surface without delivering a capability OPA lacks. Defer until either (a) `current_groups()` in raw SQL becomes a user-facing requirement, or (b) attribute derivation grows past what Rego can express cleanly.

### Combinatorial Keycloak groups (one group per site × modality × persona)

`radiologist-stl-barnes-ct-clinical`, etc. Map directly to Trino groups.

**Rejected**: combinatorial explosion. For 10 sites × 5 modalities × 3 personas the group count is 150; per-user assignment becomes a many-group operation that's hard to audit and easy to misconfigure. The attribute model (multi-valued K=V on user) collapses this to ~18 distinct attribute values that the user holds any subset of, evaluated at query time.

### End-user JWT pass-through from all clients

Superset, JupyterHub, the MCP, and Voila each forward the user's Keycloak token to Trino on every query. No service principal, no impersonation rules.

**Rejected for Superset, Voila, and the MCP**: implementation cost vs. marginal security benefit. For Superset, a custom `DB_CONNECTION_MUTATOR` (`ansible/roles/superset/files/superset_trino_auth.py`) mints a `superset_svc` JWT via Keycloak client_credentials and sets `X-Trino-User` to the logged-in user — equivalent authorization semantics to forwarding the user's Keycloak token, without the per-user token-refresh lifecycle on long dashboard sessions. Scout writes bespoke client code either way; the real distinction is service-token minting vs. pulling the user's session token through the client's session storage. The "insider with Kubernetes namespace access steals a service principal's credential" threat is addressed via K8s controls (sealed secrets, restricted RBAC on Secrets, rotation, NetworkPolicy on Trino). For the MCP, `tuannvm/mcp-trino` v4.x validates the inbound Keycloak OIDC token from Open WebUI, extracts `preferred_username`, and sets `X-Trino-User` outbound on a service-principal HTTP Basic connection — end-user identity propagates to Trino via header on a service-principal channel rather than as a forwarded JWT. Voila uses the same pattern (with a JWT-based outbound rather than Basic) because Voila playbooks are pre-defined ConfigMap-mounted code with no arbitrary-execution surface for end users. **Accepted for JupyterHub**: per-user JWT is the natural model when each notebook server is already spawned with the user's session token via `auth_state`, and the kernel runs arbitrary user code that benefits from per-call auth.

### Extending RBAC to `trino-rw`

Authenticate and authorize transformer DDL through Trino RBAC instead of the NetworkPolicy gate.

**Rejected**: `trino-rw` exists per ADR 0019 specifically to receive transformer-issued view DDL plus the Voila playbook writeback path. Its NetworkPolicy already restricts ingress to the `hl7-transformer` pod, the Voila pod, and Prometheus. There are no arbitrary user-facing queries against `trino-rw`. Adding RBAC there solves nothing and complicates the transformer's auth surface.

## Consequences

### Positive

- **Multi-tenant queries become safe by construction.** Site-based row filters mean a BJH user querying `delta.default.reports` cannot read MCBC rows even if they craft an explicit `WHERE sending_facility = 'MCBC'` predicate — the filter is appended at the planner level by Trino's OPA plugin, not by the user's SQL.
- **PHI exposure is attribute-driven.** Column masks reduce the surface of identified data for users where `mask_phi_fields` is unset or `["true"]`. The shipped masked-column list is intentionally minimal (patient names and ZIP/postal code) — the goal is to prove the masking mechanism, not full de-identification. Most schema fields carry research value (report text for clinical review, patient IDs for longitudinal tracking, dates for temporal analysis) and remain clear-text by design. The list is config-driven and extends without policy changes. Users (including admins) with `mask_phi_fields: ["false"]` see clear text on all columns.
- **Per-query attribution via OPA decision logs.** OPA emits a decision log entry for every Trino access-control evaluation, including `input.context.identity.user` (the effective end-user identity, post-impersonation). Loki picks these up; "who queried `mpi` last week" becomes a Loki query against OPA's decision-log stream.
- **Policies live in git and are tested in CI.** Rego policies in `policy/` are reviewable in PRs, runnable through `opa test` for unit coverage, and deployable via the same Ansible flow as the rest of Scout — no UI to keep in sync, no out-of-band policy edits.
- **Delegated site-admin model unlocks partner self-service.** Site admins at each partner institution manage their own users via Keycloak's fine-grained admin permissions; Scout engineering is not a bottleneck for onboarding/offboarding.
- **Real-time offboarding is a hard guarantee.** The Keycloak SPI listener republishes the OPA bundle on every user disable (ADR 0021); OPA's next bundle pull (5–10 s window) propagates to all replicas. Suspended users lose access within seconds.

### Negative

- **Trino views run with `SECURITY DEFINER` (Trino's CREATE VIEW default)** and the policy is structured around that choice. The view's underlying-table reads are evaluated as the view OWNER (`trino`), so row filters and column masks scoped to the owner would clamp the view's reads to zero rows. The policy lists `trino` in `view_owner_principals`, which exempts it from row-filter / column-mask evaluation; the invoker's RBAC is still enforced because the views themselves are in `filtered_tables` (filters are applied to the view's output, not the underlying reads). The companion `view_only_tables` deny on `reports_report_patient_mapping` is bypassed for the view owner via a `CreateViewWithSelectFromColumns` allow rule, so the `_epic_view` views' internal joins succeed while direct user queries against the mapping table fail. `SECURITY INVOKER` would have been the more naïvely-intuitive choice, but it conflicts with two requirements: (a) the join-target deny on `reports_report_patient_mapping` would propagate to the view, breaking it; (b) column masks on `m.mpi` / `m.epic_mrn` would propagate through the view's `MAX(m.epic_mrn) OVER ...` window and NULL out `resolved_epic_mrn` for everyone. DEFINER preserves both behaviors.
- **Token-TTL-vs-query-duration is a configuration concern.** Trino validates JWTs at query submission; long-running notebook flows that span multiple submissions can outlive Keycloak's default 5-minute access token TTL. Service-principal tokens (`superset_svc`, `openwebui_mcp_svc`, `voila_svc`) are issued with extended lifespan (~4 hours) to cover query duration; end-user JWT pass-through (Jupyter `auth_state`) stays at the realm default and refreshes via the refresh token between submissions.
- **Full-reinstall release.** RBAC ships as a single coordinated update across Trino, OPA, Keycloak, and every client role. The shared `trino` user is removed in the same release; any saved Superset connections, notebook configs, or dashboard queries that hardcoded it are migrated at the same time, not at a separate cutover moment.
- **New infrastructure to operate.** An OPA Deployment (small, but new) and a Keycloak SPI event listener (`keycloak/event-listener/`, packaged alongside the existing `user-approval-email` listener) join the operational surface. Both have their own lifecycle, monitoring, and patch concerns.
- **Token audience handling is a recurring gotcha.** Keycloak issues tokens with `aud=<client>` by default; clients (Superset, Jupyter, MCP) need either an explicit audience-mapper for `trino` or Trino must accept multiple audience values. Misconfiguration produces 401s with limited diagnostic information.
- **Notebook image excludes Spark.** JupyterHub and Voila share a notebook image (`ghcr.io/washu-tag/scout-notebook`) based on `scipy-notebook` + `trino-python-client` — no PySpark, no direct MinIO access. Any prior notebook that imported `pyspark` or called `spark.sql(...)` uses the Trino DB-API pattern instead.

## Implementation Notes

- **Policy location**: `policy/trino/main.rego` (rules), `policy/trino/main_test.rego` (unit tests run via `opa test`).
- **OPA topology**: 2-replica `Deployment` in `scout-analytics` namespace, exposed via ClusterIP `opa-trino.scout-analytics:8181`. Resource baseline ~100m CPU / 256 MiB per replica.
- **Trino TLS**: cert-manager-issued PKCS12 keystore mounted into the coordinator; Trino listens on `https://trino.scout-analytics:8443`. Internal CA bundle distributed to client namespaces (Superset, Jupyter, MCP, Voila) via a Secret or ConfigMap that clients mount and pass to their HTTP libraries as `verify=<ca-path>`. JWT auth requires TLS per Trino's auth model.
- **Keycloak event listener**: in-process SPI plugin (`OpaUserBundlePublisherProvider`) in `keycloak/event-listener/`, alongside the existing `user-approval-email` listener. Maintains an in-memory user-attribute snapshot and debounces admin events into S3 PUTs of a tar.gz bundle to MinIO. OPA's native bundle plugin handles distribution across replicas via independent pulls. See ADR 0021 for full design.
- **Trino impersonation rules** (`policy/trino/main.rego`, `trino_service_principals` set): `superset_svc`, `openwebui_mcp_svc`, `voila_svc`. No wildcard principal. Same set is used both for `ImpersonateUser` allow and for the `is_system_identity` carve-out that exempts service principals from the `user_enabled` gate (service principals aren't in `data.users`).
- **Ingress auth on the MCP**: `tuannvm/mcp-trino` validates the inbound Keycloak OIDC token before honoring `X-Trino-User`, so requests without a valid Keycloak-signed token are rejected at the MCP. Token-forgery resistance comes from Keycloak's signing key, not from network position.
- **View security model**: views are created `SECURITY DEFINER` (Trino's default) by the `trino` admin used by `hl7-transformer`. The policy carries a `view_owner_principals` data set whose members bypass row filters and column masks (so the owner can materialize the view), and an explicit `CreateViewWithSelectFromColumns` allow rule that exempts the owner from `view_only_blocked` (so the view's internal joins to `reports_report_patient_mapping` succeed while direct invoker queries on that table are denied).
- **`view_only_tables` join-target deny**: tables that lack `sending_facility` (e.g., `reports_report_patient_mapping`) can't be row-filtered the same way; a facility-restricted user querying them directly could enumerate every patient identifier. The policy reads an inventory-driven `view_only_tables` list and denies direct SELECT (plus hides them from `SHOW TABLES`) for non-view-owner principals. DEFINER views over those tables still work.
- **Attribute-driven filter shape**: the policy iterates `data.attribute_filters` (a map of `keycloak_attribute → {column, optional tables_override}`) rather than hardcoding `allowed_facilities → sending_facility`. Adding a restriction dimension (e.g. `allowed_modalities → modality`) is one inventory edit + an OPA reload; the rego doesn't change. The Keycloak realm template renders the corresponding User Profile attribute from the same map, so the two sides stay in lockstep.
- **`filtered_tables` is a shared list**: a single top-level `filtered_tables` list applies to every configured attribute dimension, with an optional per-attribute `tables_override`. In practice every dimension scopes to the same set of data tables (`reports`, `reports_curated`, `reports_latest`, `reports_dx`, the three `_epic_view` views), so a shared list is cleaner than per-attribute repetition.
- **Type-aware column masks**: varchar columns get `'[REDACTED]'` (visible redaction); other types get bare `NULL` (Trino's analyzer coerces to the column's declared type at evaluation). Bare `NULL` coerces cleanly even for complex types like `array(row(...))`, so no per-type CAST is needed.
- **JWT user-mapping pattern**: Keycloak service-account users have `preferred_username = service-account-<client_id>`. Trino's `http-server.authentication.jwt.user-mapping.pattern` strips the prefix so the OPA impersonation allowlist (and the `view_owner_principals` check) operate on bare `superset_svc` / `openwebui_mcp_svc` / `voila_svc` names.

## Future Considerations

- **Per-site Delta schemas** — strong logical isolation at the storage layer (one schema per partner with independent Delta transaction logs). Independent of this ADR; pursue when a tenant explicitly requires storage-level separation or when eviction tooling needs to be demonstrably clean.
- **Cohort/ACL table** for project-based access — a Scout-owned table joined into row filters via subquery. The architecture chosen here accepts this addition without rework.
- **Trino resource groups** — per-tenant CPU/memory caps to mitigate noisy-neighbor risk when query volume grows.
- **Custom Trino `GroupProvider` plugin** — defer until either (a) `current_groups()` in user-authored SQL becomes a requirement, or (b) attribute derivation logic exceeds what Rego expresses cleanly.
- **Per-tenant Keycloak realms** — stronger isolation if a partner's contract requires that their site admins cannot see other tenants' users exist. Migration from the single-realm model is non-trivial; defer until contractually driven.
- **Trino event-listener tenant-tagged audit stream** — emit a Loki stream with a `tenant` label derived from the user's `allowed_facilities` attribute (single facility → that facility code; multi-valued → `multi`; wildcard → `all`; empty → `none`). OPA decision logs cover per-query attribution today; this would add a Trino-side audit channel.

## References

- ADR 0003: OAuth2 Proxy as Authentication Middleware — UI-layer auth; this ADR is the data-layer counterpart.
- ADR 0005: MinIO STS Authentication Decision — explains why per-user MinIO STS isn't a drop-in answer for clients that need direct object-storage access.
- ADR 0011: Deployment Portability via Layered Architecture — service-mode pattern informs how OPA and Keycloak event-listener components are layered.
- ADR 0019: Read-Write Trino Instance for Transformer-Issued View DDL — establishes the dual-Trino topology; clarifies that RBAC applies only to the user-facing Trino in `scout-analytics`.
- ADR 0021: OPA User Attribute Distribution via MinIO Bundles — the distribution mechanism this ADR's "Attribute distribution" row references.
- Trino docs: OPA access control — <https://trino.io/docs/current/security/opa-access-control.html>
- Trino docs: JWT authentication — <https://trino.io/docs/current/security/jwt.html>
