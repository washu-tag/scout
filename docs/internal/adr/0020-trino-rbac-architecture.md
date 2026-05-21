# ADR 0020: Trino RBAC via OPA with Keycloak Attributes

**Date**: 2026-05-15 (proposed) / 2026-05-19 (accepted)
**Status**: Accepted
**Decision Owner**: TAG Team

> **Reader note**: the architecture in this ADR shipped, but several
> details changed during implementation. See [Implementation
> deviations](#implementation-deviations) at the end of the document
> for the most important differences from the original proposal —
> notably **SECURITY DEFINER (not INVOKER) for the `_epic_view`
> tables**, the **`view_owner_principals` exemption** that pairs with
> it, and **deferred items** (real-time cache invalidation, MCP, Voila).

## Context

Scout's user-facing Trino instance (`trino-analytics`) today has **no authentication and no authorization**. Every caller — Superset, JupyterHub, Open WebUI's MCP tool, ad-hoc `kubectl exec` users — connects as the hardcoded user `trino`. Read-only behavior is enforced exclusively at the connector layer (`delta.security=READ_ONLY`), at the metastore (read-only PostgreSQL role on `hive_metastore_endpoint_readonly`), and at MinIO (`s3_lake_reader` bucket policy). These prevent writes; they do not constrain *what data* a given user can read. Every user sees every row of every report from every facility.

This was an acceptable posture while Scout was a single-tenant deployment whose only access control was cluster-network membership. It is not acceptable for the platform's direction: a multi-tenant analytics layer over HL7 radiology reports from multiple partner institutions, each of which must see only their own data and which may further partition access by clinical vs. research persona, by modality sub-specialty, and (eventually) by cohort/IRB project.

### Requirements driving this ADR

1. **Per-user row-level access** to the `reports` family of tables, scoped by `sending_facility` at minimum.
2. **PHI column masking** Doing this to perfection is probably not feasible, but we could redact contents of certain columns to reduce risk. E.g., if we had derived attributes about reports, perhaps we'd redact all report text columns, non-Scout patient ids, dates, etc.
3. **Real-time onboarding and offboarding.** A user disabled at their partner institution must lose access within seconds, not at the next sync interval. This is a stated security requirement.
4. **Multi-client identity.** Superset, JupyterHub, and Open WebUI's Trino MCP must each propagate the actual end-user identity to Trino; today they all connect as a shared service principal.
5. **No combinatorial group sprawl in Keycloak.** Modeling N sites × M modalities × K personas as N×M×K Keycloak groups is operationally untenable.
6. **GitOps-friendly policy.** Policy changes are reviewable in pull requests and tested in CI, like the rest of the codebase.

### What this ADR does *not* cover

- **PySpark in JupyterHub** previously bypassed Trino by reading Delta files directly from MinIO with shared `s3a` credentials. **This release removes Spark and direct MinIO access from the notebook image entirely** — notebooks now use `trino-python-client` exclusively, so every notebook read is governed by Trino's OPA-enforced RBAC. The quickstart notebook is rewritten to demonstrate the Trino-only pattern. Voila playbooks (a separate service) still use Spark and are out of scope for this ADR.
- **Storage-layer multitenancy** (per-site Delta schemas, per-tenant buckets) is independent of Trino RBAC. Strong logical isolation at the storage layer is also a future ADR; this one is solely about query-time RBAC.
- **The write-enabled `trino-rw` instance** stays gated by NetworkPolicy per ADR 0019. RBAC applies only to the user-facing `trino-analytics`.
- **Cohort/project-based access** (membership lists managed by PIs) is deferred until the use case materializes. The architecture chosen here supports adding it later via a Trino ACL table without rework, but we don't even have a cohort entity today.

## Decision

**Trino enforces RBAC via Open Policy Agent (OPA). User identity is established via Keycloak-issued JWT (programmatic clients). Authorization attributes are stored on the user object in Keycloak; OPA fetches them at policy-evaluation time and caches with a Keycloak event-listener-driven invalidation for real-time offboarding. Per-client identity propagation uses the impersonation pattern (Superset, Open WebUI MCP) or JWT pass-through (JupyterHub), depending on which mechanism each client supports natively.**

### Architecture summary

| Layer | Choice |
|---|---|
| Authentication | `http-server.authentication.type=JWT` — client traffic on HTTPS port 8443 (TLS cert from cert-manager). The chart's HTTP listener on port 8080 remains enabled for worker↔coordinator internal-communication (authenticated via `internal-communication.shared-secret`, no JWT) and Kubernetes probes (`/v1/info` is unauthenticated). HTTPS-only deployment is not supported by the Trino chart. Trino web UI not exposed externally |
| Authorization | `access-control.name=opa`; `delta.security=SYSTEM` (changed from `READ_ONLY` on `trino-analytics`) |
| Identity model | Existing `scout-admin` and `scout-user` Keycloak groups continue to gate admin/user permissions in other Scout apps; OPA's Trino policy itself is **purely attribute-driven** (no group check). Per-user attributes: `allowed_facilities` (multivalued, supports `*` wildcard) for row scoping; `mask_phi_fields` (boolean, default-mask) for column masking. Admins set their own attributes to test restricted views; opt into unrestricted access via `allowed_facilities: ["*"]` + `mask_phi_fields: "false"` |
| Attribute fetch | OPA `http.send` to Keycloak admin API, cached (~60 s TTL) |
| Real-time invalidation | *(Deferred from v1)* Keycloak event listener pushes cache-busts to OPA on user update/disable. v1 ships with the 60 s TTL only; manual OPA pod restart for immediate revocation. |
| Policy language | Rego, in `policy/` at the repo root; `opa test` in CI |
| OPA topology | 2-replica Deployment behind ClusterIP in `scout-analytics` namespace |
| Superset → Trino | Built-in impersonation; Superset authenticates as `superset_svc` (Keycloak client_credentials) |
| JupyterHub → Trino | JupyterHub `auth_state` exposes Keycloak access token to spawned notebooks; clients use `JWTAuthentication` |
| Voila → Trino | *(Deferred from v1)* Voila authenticates as `jupyter_svc` (Keycloak client_credentials) and impersonates the session user via `X-Trino-User`. The `jupyter_svc` Keycloak client and the OPA impersonation allowlist entry are pre-provisioned. |
| Open WebUI MCP → Trino | *(Deferred from v1)* MCP authenticates as `openwebui_mcp_svc`; reads `X-OpenWebUI-User-Email` and sets `X-Trino-User`; gated by NetworkPolicy. v1 ships with the Keycloak client + OPA impersonation rule provisioned, but the MCP itself isn't wired through to user-specific identities. `tuannvm/mcp-trino` only supports HTTP Basic outbound — wiring it requires enabling Trino `PASSWORD,JWT` dual auth. |
| Audit | *(Deferred from v1)* Trino event listener → Loki, with `tenant` tag derived from user's `allowed_facilities` attribute. v1 relies on OPA's decision logs + Trino's standard event-log; the custom tenant tag is a follow-up. |
| Release model | Full coordinated release across all client roles; no flag-gated rollout or dual-auth window |

## Alternatives Considered

### File-based system access control (`access-control.name=file`)

JSON rules file with row filters and column masks. No new runtime; well-documented.

**Rejected**: scales poorly for the policy complexity we need. The expressions allowed in `filter` and `mask` fields are single SQL expressions per rule — fine for simple cases but awkward for conditional masking (clinical-vs-research persona logic), unwieldy for cohort-style subqueries, and untestable as a unit. The deeper problem is that the attribute model lives in Keycloak; file rules require those attributes to be materialized into Trino's group provider, which means either (a) a custom group provider plugin, or (b) a cronjob materializing a `groups.txt` file — and the user has stated that file-from-cron is insufficient for real-time offboarding. OPA's `http.send` solves this without a custom plugin.

### Apache Ranger

Centralized policy server with web UI, row filters, column masks, built-in audit. Available as a Trino plugin (in-tree since Trino 466).

**Rejected**: operational footprint disproportionate to the deployment. Ranger requires a Ranger Admin webapp, a relational DB for policy storage, Solr or Elasticsearch for audit, and typically UserSync against LDAP/AD. For a single-engine deployment (Trino only) maintained by a small team using GitOps for everything else, the infrastructure burden, the UI-as-source-of-truth model, and the limited policy testing story dominate the benefits. Reconsider only if Scout adds direct Hive/Spark/Kafka authz needs or grows a security team that authors policy outside engineering.

### SQL standard access control (`hive.security=sql-standard`, `GRANT`/`REVOKE`)

In-band `GRANT SELECT ON ... TO ROLE radiologists` syntax stored in metastore ACL tables.

**Rejected**: the Delta Lake connector does not accept `delta.security=sql-standard` — supported values are `ALLOW_ALL`, `SYSTEM`, `READ_ONLY`, `FILE`. Even sharing the Hive metastore, Delta tables do not inherit Hive's GRANT/REVOKE semantics. Not an option for Scout's silver layer.

### Custom Trino `GroupProvider` plugin

JVM plugin that, given a username, fetches groups from Keycloak's admin API in real time. Pairs with file-based or OPA authz.

**Rejected** for v1: the work the plugin would do — fetch user attributes from Keycloak, cache for ~60 s — is exactly what OPA's `http.send` already does declaratively in Rego. Building, packaging, and maintaining a Java plugin (with its own breaking-change exposure to the Trino SystemAccessControl/GroupProvider SPIs) adds engineering surface without delivering a capability OPA lacks. Defer until either (a) `current_groups()` in raw SQL becomes a user-facing requirement, or (b) attribute derivation grows past what Rego can express cleanly.

### Combinatorial Keycloak groups (one group per site × modality × persona)

`radiologist-stl-barnes-ct-clinical`, etc. Map directly to Trino groups.

**Rejected**: combinatorial explosion. For 10 sites × 5 modalities × 3 personas the group count is 150; per-user assignment becomes a many-group operation that's hard to audit and easy to misconfigure. The attribute model (multi-valued K=V on user) collapses this to ~18 distinct attribute values that the user holds any subset of, evaluated at query time.

### End-user JWT pass-through from all clients

Superset, JupyterHub, and the MCP each forward the user's Keycloak token to Trino on every query. No service principal, no impersonation rules.

**Rejected for Superset and the MCP**: implementation cost vs. marginal security benefit. Superset's built-in impersonation, plus a properly authenticated service principal on the Trino connection, gives equivalent authorization semantics for every threat model except "insider with Kubernetes namespace access steals the `superset_svc` credential" — a threat better addressed through K8s controls (sealed secrets, restricted RBAC on Secrets, rotation, NetworkPolicy on Trino) than through bespoke Superset code that pulls Keycloak tokens out of session state and forwards them. The MCP situation is similar: `mcpo` does not store OAuth tokens per-user (it caches them per server), so per-user JWT pass-through through the MCP path is not natively available today; trusting `X-OpenWebUI-User-Email` from a network-policy-gated MCP is the practical equivalent. **Accepted for JupyterHub**: per-user JWT is the natural model when each notebook server is already spawned with the user's session token via `auth_state`.

### Extending RBAC to `trino-rw`

Authenticate and authorize transformer DDL through Trino RBAC instead of the NetworkPolicy gate.

**Rejected**: `trino-rw` exists per ADR 0019 specifically to receive transformer-issued view DDL. Its NetworkPolicy already restricts ingress to the `hl7-transformer` pod and Prometheus. There are no user-facing queries against `trino-rw`. Adding RBAC there solves nothing and complicates the transformer's auth surface.

## Consequences

### Positive

- **Multi-tenant queries become safe by construction.** Site-based row filters mean a BJH user querying `delta.default.reports` cannot read MCBC rows even if they craft an explicit `WHERE sending_facility = 'MCBC'` predicate — the filter is appended at the planner level by Trino's OPA plugin, not by the user's SQL.
- **PHI exposure becomes attribute-driven.** Column masks reduce the surface of identified data for users where `mask_phi_fields` is unset or `"true"`. The v1 masked-column list is intentionally minimal (patient names and ZIP/postal code) — the goal is to prove the masking mechanism, not full de-identification. Most schema fields carry research value (report text for clinical review, patient IDs for longitudinal tracking, dates for temporal analysis) and remain clear-text by design. The list is config-driven and extends without policy changes. Users (including admins) with `mask_phi_fields: "false"` see clear text on all columns.
- **Per-query attribution.** Trino's audit log captures `principal` (the authenticated service identity when impersonation is in use) and `user` (the effective end-user identity). Loki receives both fields; "who queried `mpi` last week" becomes a Loki query.
- **Policies live in git and are tested in CI.** Rego policies in `policy/` are reviewable in PRs, runnable through `opa test` for unit coverage, and deployable via the same Ansible flow as the rest of Scout — no UI to keep in sync, no out-of-band policy edits.
- **Delegated site-admin model unlocks partner self-service.** Site admins at each partner institution manage their own users via Keycloak's fine-grained admin permissions; Scout engineering is not a bottleneck for onboarding/offboarding.
- **Real-time offboarding is a hard guarantee.** The Keycloak event listener pushes invalidations to OPA's cache on every user disable, so suspended users lose access within seconds.

### Negative

- **`delta.security=READ_ONLY` is replaced by `delta.security=SYSTEM` on `trino-analytics`.** The defense-in-depth read-only posture (per ADR 0019) loses one layer at the connector. Writes are still blocked at the metastore (read-only PostgreSQL role) and at MinIO (read-only bucket credentials), but Trino's catalog itself no longer hardcodes read-only. Acceptable because OPA policy enforces it at a finer grain, but worth noting that "Trino is read-only by configuration" stops being true.
- **Trino views run with `SECURITY DEFINER` (Trino's CREATE VIEW default)** and the policy is structured around that choice. The view's underlying-table reads are evaluated as the view OWNER (`trino`), so row filters and column masks scoped to the owner would clamp the view's reads to zero rows. The policy lists `trino` in `view_owner_principals`, which exempts it from row-filter / column-mask evaluation; the invoker's RBAC is still enforced because the views themselves are in `filtered_tables` (filters are applied to the view's output, not the underlying reads). The companion `view_only_tables` deny on `reports_report_patient_mapping` is bypassed for the view owner via the `CreateViewWithSelectFromColumns` allow rule, so the `_epic_view` views' internal joins still succeed while direct user queries against mapping fail. The original proposal called for `SECURITY INVOKER`, but that conflicted with two requirements: (a) the join-target deny on mapping would propagate to the view, breaking it; (b) column masks on `m.mpi` / `m.epic_mrn` would propagate through the view's `MAX(m.epic_mrn) OVER ...` and NULL out `resolved_epic_mrn` for everyone. DEFINER preserves both behaviors.
- **Token-TTL-vs-query-duration becomes a configuration concern.** Trino validates JWTs at query submission; long-running notebook or Spark-via-Trino flows that span multiple submissions can outlive Keycloak's default 5-minute access token TTL. Service-principal tokens (`superset_svc`, `jupyter_svc`, `openwebui_mcp_svc`) are issued with extended lifespan (~4 hours) to cover query duration; end-user JWT pass-through (Jupyter `auth_state`) stays at the realm default and refreshes via the refresh token between submissions. The implementation plan specifies per-client TTL.
- **Full-reinstall release.** RBAC ships as a single coordinated update across Trino, OPA, Keycloak, and every client role. The shared `trino` user is removed in the same release; any saved Superset connections, notebook configs, or dashboard queries that hardcoded it are migrated as part of the release rather than at a separate cutover moment. The implementation plan inventories these consumers before release.
- **New infrastructure to operate.** An OPA Deployment (small, but new) and a Keycloak event listener component (location TBD in the implementation plan) join the operational surface. Both have their own lifecycle, monitoring, and patch concerns.
- **Token audience handling is a recurring gotcha.** Keycloak issues tokens with `aud=<client>` by default; clients (Superset, Jupyter, MCP) need either an explicit audience-mapper for `trino` or Trino must accept multiple audience values. Misconfiguration produces 401s with limited diagnostic information. The implementation plan documents the per-client mapper setup.
- **Notebook image loses Spark.** The previous `pyspark-notebook`-based image is replaced with `scipy-notebook` plus `trino-python-client`. Existing notebooks that import `pyspark` or call `spark.sql(...)` need to be migrated to the Trino DB-API pattern shown in the new quickstart. The Voila playbooks service is unaffected.

## Implementation Notes

- **Policy location**: `policy/` at the root of scout-demo. Subdirectories per concern (`policy/trino/`, `policy/trino/test/`).
- **OPA topology**: 2-replica `Deployment` in `scout-analytics` namespace, exposed via ClusterIP `opa.scout-analytics:8181`. Resource baseline ~100m CPU / 256 MiB per replica.
- **Trino TLS**: cert-manager-issued PKCS12 keystore mounted into the coordinator; Trino listens on `https://trino.scout-analytics:8443`. Internal CA bundle distributed to client namespaces (Superset, Jupyter, MCP) via a ConfigMap that clients mount and pass to their HTTP libraries as `verify=<ca-path>`. JWT auth requires TLS per Trino's auth model.
- **Keycloak event listener**: design choice between an in-process Keycloak SPI plugin and a sidecar consuming the Keycloak admin events API is open at the time of this ADR; the implementation plan picks one. The contract is "on user-update or user-disable, invalidate any cached attribute lookup keyed on that user's username in OPA's `http.send` cache." OPA does not natively expose per-key cache invalidation; the implementation plan documents the chosen pattern.
- **Trino impersonation rules** (in Rego): only `superset_svc`, `jupyter_svc` (if used), and `openwebui_mcp_svc` are permitted impersonators. No wildcard principal.
- **Network gate on the MCP**: a NetworkPolicy in `ansible/roles/open-webui/` restricts ingress to the MCP to the Open WebUI pod only, eliminating the "forge the `X-OpenWebUI-User-Email` header from another pod" attack.
- **Audit tag derivation**: the `tenant` label on Loki entries is derived from the user's `allowed_facilities` attribute at audit-event-emit time. Single facility → that facility code; multi-valued → `multi`; wildcard (`*`) → `all`; empty/unset → `none`. Group membership is captured separately in the audit record (so admin actions remain queryable) but does not affect the tenant tag.
- **Implementation plan**: separate document tracking work units (Phases 0–5: Keycloak prerequisites → Trino auth → OPA scaffolding → identity propagation → row filters → column masks + audit). Phases are PR-shaped work units within a single coordinated release, not deployment phases — there is no flag-gated rollout.

## Implementation deviations

Changes from the original proposal worth knowing before reading the policy.
The deviations are additive — none alter the threat model, but they affect
how a reader interprets the rego and the inventory shape.

- **View security model flipped from `SECURITY INVOKER` to `SECURITY DEFINER`.**
  See the "Trino views" bullet in [Negative consequences](#negative) above
  for the full reasoning. The policy adds two pieces to make this work:
  a `view_owner_principals` data set whose members bypass row filters and
  column masks (so the view-owner identity, `trino`, can materialize the
  view), and an explicit `CreateViewWithSelectFromColumns` allow rule that
  bypasses `view_only_blocked` (so the view's internal join to mapping
  succeeds while direct invoker queries on mapping fail).

- **`view_only_tables` join-target deny.** The mapping table
  (`reports_report_patient_mapping`) doesn't have `sending_facility` and
  can't be row-filtered the same way; a facility-restricted user could
  enumerate every patient identifier by selecting from it directly. The
  policy adds an inventory-driven `view_only_tables` list. Tables in
  that list are denied for direct SELECT and hidden from `SHOW TABLES`,
  but reachable via DEFINER views (per the bullet above).

- **Generic `attribute_filters` shape replaced per-attribute hardcoding.**
  Rather than embedding `allowed_facilities → sending_facility` in the
  rego, the policy iterates over `data.attribute_filters` (a map of
  `keycloak_attribute → {column, optional tables_override}`). Adding a
  restriction dimension (e.g. `allowed_modalities → modality`) is one
  inventory edit + an OPA reload; the rego doesn't change. The Keycloak
  realm template renders the corresponding User Profile attribute from
  the same map, so the two sides stay in lockstep.

- **`trino_filtered_tables` is a shared list, not per-attribute.**
  Originally each `attribute_filters` entry carried its own `tables`
  list. In practice every dimension scopes to the same set of data
  tables (`reports`, `reports_curated`, `reports_latest`, `reports_dx`,
  the three `_epic_view` views), so a single top-level list with an
  optional per-attribute override is cleaner.

- **Column masks are type-aware.** Varchar columns get `'[REDACTED]'`
  (visible redaction); anything else gets bare `NULL` (Trino's analyzer
  coerces to the column's declared type at evaluation). `full_patient_name`
  (array of row) and a future complex-typed PHI column don't need a
  CAST that reflects Trino's serialized type string back into the policy
  — empirically NULL coerces cleanly even for `array(row(...))`.

- **`hidden_columns` was tried and removed.** An intermediate design
  filtered complex-typed PHI columns out of the user's visible column
  set instead of masking them. It broke `SELECT *` (Trino expands the
  star at parse time and runs SelectFromColumns with the full column
  list before FilterColumns gets a chance to prune). Bare-NULL masking
  works without that tradeoff.

- **JWT user-mapping pattern.** Keycloak service-account users have
  `preferred_username = service-account-<client_id>`. Trino's
  `http-server.authentication.jwt.user-mapping.pattern` strips the
  prefix so the OPA impersonation allowlist (and the
  `view_owner_principals` check) can use bare `superset_svc` /
  `jupyter_svc` / `openwebui_mcp_svc` names.

- **`mcp-trino` outbound capability.** The original ADR assumed the MCP
  could use JWT outbound. `tuannvm/mcp-trino` v4.x only supports HTTP
  Basic outbound; wiring full MCP integration will require enabling
  Trino's `PASSWORD,JWT` dual-auth mode and giving `openwebui_mcp_svc`
  a Trino password. The Keycloak client + OPA impersonation rule are
  provisioned; the Trino-side dual auth is the remaining work.

- **Spark removal from Jupyter notebook image** shipped as part of this
  ADR (notebooks use `trino-python-client` only). The CA cert plumbing
  in the singleuser pod required including Scout's internal CA in the
  combined trust bundle that `REQUESTS_CA_BUNDLE` points at — without
  that, the air-gapped staging cert bundle silently overrode per-call
  `verify=` kwargs.

## Future Considerations

- **Voila playbooks Spark bypass** — Voila still uses Spark + direct MinIO access. The same Trino-only migration could be applied to the Voila playbook image and any Spark-dependent playbooks, or per-user MinIO STS could be retrofitted there if Voila needs to keep Spark. Deferred to a future decision.
- **Per-site Delta schemas** — strong logical isolation at the storage layer (one schema per partner with independent Delta transaction logs). Independent of this ADR; pursue when a tenant explicitly requires storage-level separation or when eviction tooling needs to be demonstrably clean.
- **Cohort/ACL table** for project-based access — a Scout-owned table joined to in row filters via subquery. The architecture chosen here accepts this addition without rework.
- **Trino resource groups** — per-tenant CPU/memory caps to mitigate noisy-neighbor risk when query volume grows.
- **Custom Trino `GroupProvider` plugin** — defer until either (a) `current_groups()` in user-authored SQL becomes a requirement, or (b) attribute derivation logic exceeds what Rego expresses cleanly.
- **Per-tenant Keycloak realms** — stronger isolation if a partner's contract requires that their site admins cannot see other tenants' users exist. Migration from the single-realm model is non-trivial; defer until contractually driven.

## References

- ADR 0003: OAuth2 Proxy as Authentication Middleware — UI-layer auth; this ADR is the data-layer counterpart.
- ADR 0005: MinIO STS Authentication Decision — explains why per-user MinIO STS isn't a drop-in answer; relevant if Voila ever needs the same Spark-bypass remediation.
- ADR 0011: Deployment Portability via Layered Architecture — service-mode pattern informs how OPA and Keycloak event-listener components are layered.
- ADR 0019: Read-Write Trino Instance for Transformer-Issued View DDL — establishes the dual-Trino topology; clarifies that RBAC applies only to `trino-analytics`.
- Trino docs: OPA access control — <https://trino.io/docs/current/security/opa-access-control.html>
- Trino docs: JWT authentication — <https://trino.io/docs/current/security/jwt.html>
