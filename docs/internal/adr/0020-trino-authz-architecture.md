# ADR 0020: Trino Authorization via OPA with Keycloak Attributes

**Date**: 2026-05-19
**Status**: Accepted
**Decision Owner**: TAG Team

## Context

Scout's user-facing Trino instance in the pre-AuthZ state connects every caller as the hardcoded user `trino` — no row-level access control, no column masking, no notion of identity at the data layer. This was acceptable for a single-tenant deployment whose only access control was cluster-network membership. It is not acceptable as different user-types use the platform, with different IRB allowances. It also doesn't support multitenancy.

This ADR picks the authorization engine and the policy data model. The distribution of per-user attributes from Keycloak to OPA is covered by [ADR 0021](0021-opa-user-attribute-distribution.md). The authentication and identity-propagation pieces (JWT auth, `X-Trino-User` impersonation, per-client patterns) are covered by [ADR 0022](0022-trino-auth-and-impersonation.md). View security (the `SECURITY DEFINER` model, `view_owner_principals` exemption, `hidden_tables` join-target deny) is covered by [ADR 0023](0023-trino-view-security-model.md).

### Requirements driving this ADR

1. **Per-user row-level access** to the `reports` family of tables, scoped by `sending_facility` at minimum, extensible to additional dimensions (modality, persona, eventual cohort).
2. **PHI column masking.** Doing this to perfection is probably not feasible, but redacting contents of certain columns reduces risk (e.g., names, report text, non-Scout patient ids, dates) for certain classes of users.
3. **Real-time onboarding and offboarding.** Users access changes should take effect in close to real time.
4. **No combinatorial group sprawl in Keycloak.** Modeling N sites × M modalities × K personas as N×M×K Keycloak groups is operationally untenable.
5. **GitOps-friendly policy.** Policy changes are reviewable in pull requests and tested in CI, like the rest of the codebase.

### What this ADR does *not* cover

- **OPA user-attribute distribution** (bundle from MinIO) — see [ADR 0021](0021-opa-user-attribute-distribution.md).
- **Authentication and identity propagation** — see [ADR 0022](0022-trino-auth-and-impersonation.md).
- **View security model** (DEFINER vs INVOKER, the `view_owner_principals` exemption, `hidden_tables` join-target deny) — see [ADR 0023](0023-trino-view-security-model.md).
- **Storage-layer multitenancy** (per-site Delta schemas, per-tenant buckets) is a future ADR.
- **Cohort/project-based access** is deferred until the use case materializes.
- **The write-enabled `trino-rw` instance** stays gated by NetworkPolicy per ADR 0019. The authorization layer described here applies only to the user-facing Trino in `scout-analytics`.

### Security-model scope and non-goals

This authorization layer governs **live queries** only. It constrains what rows and columns a user's Trino query returns at execution time; it does **not** retroactively scrub or track data a user has already materialized elsewhere. Once a result set leaves Trino — exported to a notebook file, a downloaded CSV, a Superset dataset cache, or otherwise handed to another user — it is outside this layer's control.

Column masking is deliberately narrow: it redacts only the columns in `trino_masked_columns` (shipped default: `patient_name`, `full_patient_name`, `zip_or_postal_code`), and only for users an admin has opted into masking (`redact_select_identifiers="true"`; off by default). It is **not** a de-identification pass — date of birth, medical record number, the free-text `report_text` body, and other identifiers are not masked. Row filtering (`sending_facility`) is the primary confidentiality control; masking is a supplementary redaction an admin grants where those specific columns should be hidden.

## Decision

**Trino authorization uses Open Policy Agent (OPA) via Trino's in-tree `opa` access-control plugin. Authorization is attribute-driven (not group-based): per-user attributes are stored on the user object in Keycloak as User Profile entries, and the Rego policy reads them from `data.users[user]` at decision time. The policy data shape is inventory-driven — adding a restriction dimension (e.g., `allowed_modalities → modality`) is one inventory edit, not a Rego change.**

### Architecture summary

| Layer | Choice |
|---|---|
| Engine | OPA via `access-control.name=opa`. 2-replica `Deployment` in `scout-analytics`, exposed via ClusterIP `opa-trino.scout-analytics:8181`. Resource baseline per replica: requests 50m CPU / 64 MiB, limits 500m CPU / 256 MiB. |
| Connector defense | `delta.security=READ_ONLY` retained at the connector as defense in depth; OPA is the primary gate. Combined with the metastore-level read-only PostgreSQL role and MinIO `s3_lake_reader` credentials, writes are blocked at three layers. |
| Identity model | Per-user AuthZ attributes are Keycloak User Profile entries stored as `Map<String, List<String>>`: `allowed_facilities` (multivalued, supports `*` wildcard) for row scoping; `redact_select_identifiers` (multivalued, defaults to no masking; `["true"]` opts in) for column masking. The policy is otherwise attribute-driven — no group check inside row-filter / column-mask logic — except for a single gate (see Approval gate row). |
| Approval gate | The `user_enabled` rule requires both (a) `enabled: true` on the user's bundle entry (from Keycloak's `UserModel.isEnabled()`) AND (b) membership in `scout-user` or `scout-admin`. The approved-group set is hardcoded in `policy/trino/main.rego` because the same group names are hardcoded in the Keycloak realm template (`ansible/roles/keycloak/templates/scout-realm.json.j2`) — same team, same change. The group check exists because Keycloak federates from upstream IdPs: a user can land in the realm with `enabled=true` purely from a successful upstream OIDC login, before going through Scout's approval flow that adds them to `scout-user`. Service principals (`superset_svc`, `openwebui_mcp_svc`, `voila_svc`) bypass the gate via the `is_system_identity` carve-out since they never appear in `data.users`. |
| Policy data shape | `data.attribute_filters`: map of `keycloak_attribute → {column, optional tables_override}`. `data.filtered_tables`: shared list of tables every dimension applies to (with optional per-attribute override). `data.masked_columns`: list of columns masked only when `redact_select_identifiers` is `["true"]`. Adding a restriction dimension is one inventory edit; the rego doesn't change. The Keycloak realm template renders the corresponding User Profile attribute from the same map, so the two sides stay in lockstep. |
| Attribute distribution | Keycloak SPI listener publishes user attributes as an OPA bundle to MinIO; OPA's native bundle plugin pulls every 5–10 s. The Rego policy reads `data.users[user]` directly — no `http.send`, no admin-API client at decision time. See [ADR 0021](0021-opa-user-attribute-distribution.md). |
| Identity propagation | Per-client JWT or `X-Trino-User` impersonation patterns establish `input.context.identity.user` at the policy boundary. See [ADR 0022](0022-trino-auth-and-impersonation.md). |
| Policy language | Rego, in `policy/` at the repo root; `opa test policy/` in CI against the same OPA version deployed in production. |
| Audit | OPA emits a decision log entry for every Trino access-control evaluation, including `input.context.identity.user` (the effective end-user identity, post-impersonation). Loki picks these up; "who queried `mpi` last week" becomes a Loki query against OPA's decision-log stream. |

## Alternatives Considered

### File-based system access control (`access-control.name=file`)

JSON rules file with row filters and column masks. No new runtime; well-documented.

**Rejected**: scales poorly for the policy complexity we need. Our user attribute model lives in Keycloak. File rules would require those attributes be materialized into Trino's group provider, which means either (a) a custom group provider plugin, or (b) a cronjob materializing a `groups.txt` file — and file-from-cron is insufficient for real-time offboarding. OPA solves this without a custom Trino plugin: a Keycloak SPI listener publishes user attributes as an OPA bundle to MinIO, OPA pulls it natively (ADR 0021), and the rego reads `data.users[user]` at decision time. Additionally, the expressions allowed in `filter` and `mask` fields are single SQL expressions per rule. 

### Apache Ranger

Centralized policy server with web UI, row filters, column masks, built-in audit. Available as a Trino plugin (in-tree since Trino 466).

**Rejected**: operational footprint disproportionate to the deployment. Ranger requires a Ranger Admin webapp, a relational DB for policy storage, Solr or Elasticsearch for audit, and typically UserSync against LDAP/AD. For a single-engine deployment (Trino only) maintained by a small team using GitOps for everything else, the infrastructure burden, the UI-as-source-of-truth model, and the limited policy testing story dominate the benefits. Reconsider only if Scout adds direct Hive/Spark/Kafka authz needs or grows a security team that authors policy outside engineering.

### SQL standard access control (`hive.security=sql-standard`, `GRANT`/`REVOKE`)

In-band `GRANT SELECT ON ... TO ROLE radiologists` syntax stored in metastore ACL tables.

**Rejected**: the Delta Lake connector does not accept `delta.security=sql-standard` — supported values are `ALLOW_ALL`, `SYSTEM`, `READ_ONLY`, `FILE`. Even sharing the Hive metastore, Delta tables do not inherit Hive's GRANT/REVOKE semantics. Not an option for Scout's silver layer.

### Custom Trino `GroupProvider` plugin

JVM plugin that, given a username, fetches groups from Keycloak's admin API in real time. Pairs with file-based or OPA authz.

**Rejected**: the work the plugin would do — surface per-user attributes to Trino — is delivered without a Trino plugin by the bundle pipeline (Keycloak SPI → MinIO → OPA, ADR 0021). Building, packaging, and maintaining a Java plugin (with its own breaking-change exposure to the Trino SystemAccessControl/GroupProvider SPIs) adds engineering surface without delivering a capability OPA lacks. Defer until either (a) `current_groups()` in raw SQL becomes a user-facing requirement, or (b) attribute derivation grows past what Rego can express cleanly.

### Combinatorial Keycloak groups (one group per site × modality × persona)

`radiologist-stl-barnes-ct-clinical`, etc. Map directly to Trino groups.

**Rejected**: combinatorial explosion. For 10 sites × 5 modalities × 3 personas the group count is 150; per-user assignment becomes a many-group operation that's hard to audit and easy to misconfigure. The attribute model (multi-valued K=V on user) collapses this to ~18 distinct attribute values that the user holds any subset of, evaluated at query time.

### Extending OPA-enforced authorization to `trino-rw`

Authenticate and authorize transformer DDL through Trino AuthZ instead of the NetworkPolicy gate.

**Rejected**: `trino-rw` exists per ADR 0019 to receive transformer-issued view DDL plus the Voila playbook annotation writeback path. Its NetworkPolicy already restricts ingress to the `hl7-transformer` pod, the Voila pod, and Prometheus. There are no arbitrary user-facing queries against `trino-rw`. Adding AuthZ there solves nothing and complicates the transformer's auth surface.

## Consequences

### Positive

- **Multi-tenant/Site-specific queries become safe by construction.** Row filters mean a user querying `delta.default.reports` cannot read restricted rows even if they craft an explicit `WHERE sending_facility = '<forbidden site>'` predicate: the filter is appended at the planner level by Trino's OPA plugin, not by the user's SQL.
- **PHI exposure is attribute-driven.** Column masks reduce the surface of identified data for users where `redact_select_identifiers` is `["true"]` (opt-in; off by default). The shipped masked-column list is intentionally minimal (patient names and ZIP/postal code): the goal is to prove the masking mechanism, not full de-identification. The list is config-driven and extends without policy changes.
- **Per-query attribution via OPA decision logs.** Every access-control evaluation produces a log entry carrying the post-impersonation user; "who queried `mpi` last week" is a Loki query against the OPA decision stream.
- **Policies live in git and are tested in CI.** Rego policies in `policy/` are reviewable in PRs, runnable through `opa test` for unit coverage, and deployable via the same Ansible flow as the rest of Scout.
- **Adding a restriction dimension is one inventory edit.** The Rego iterates `data.attribute_filters` generically; appending `{ allowed_modalities → {column: "modality"} }` to inventory + reloading OPA is the entire change. The Keycloak realm template renders the same map into a User Profile attribute, so Keycloak and OPA stay in lockstep.

### Negative

- **New infrastructure to operate.** An OPA Deployment (small, but new) and a Keycloak SPI event listener (`keycloak/event-listener/`, packaged alongside the existing `user-approval-email` listener) join the operational surface. Both have their own lifecycle, monitoring, and patch concerns.
- **Policy authoring requires Rego literacy.** Reviewing a row-filter change means reading Rego. CI helps (`opa test policy/`) but the team grows a dependency on Rego competence.

## Implementation Notes

- **Policy location**: `policy/trino/main.rego` (rules), `policy/trino/main_test.rego` (unit tests run via `opa test`).
- **Inventory shape**: `trino_attribute_filters` (map), `trino_filtered_tables` (list), `trino_hidden_tables` (list), `trino_masked_columns` (list), `trino_view_owner_principals` (list). The keycloak role's realm template and the opa role's data document both consume `trino_attribute_filters` so the two sides stay in lockstep.
- **`opa test policy/` runs in CI** as part of `ansible-and-python-tests`. The bundle plugin's MinIO fetch is integration-tested in the data-authorization smoke suite (`tests/data-authorization/`).

## Future Considerations

- **Per-site Delta schemas** — strong logical isolation at the storage layer (one schema per site with independent Delta transaction logs). Independent of this ADR; pursue when a tenant explicitly requires storage-level separation or when eviction tooling needs to be demonstrably clean.
- **Cohort/ACL table** for project-based access — a Scout-owned table joined into row filters via subquery. The architecture chosen here accepts this addition without rework.
- **Trino resource groups** — per-tenant CPU/memory caps to mitigate noisy-neighbor risk when query volume grows.
- **Custom Trino `GroupProvider` plugin** — defer until either (a) `current_groups()` in user-authored SQL becomes a requirement, or (b) attribute derivation logic exceeds what Rego expresses cleanly.
- **Per-tenant Keycloak realms** — stronger isolation if a site's contract requires that their site admins cannot see other tenants' users exist. Migration from the single-realm model is non-trivial; defer until contractually driven.

## References

- ADR 0011: Deployment Portability via Layered Architecture — service-mode pattern informs how OPA and the Keycloak event-listener component are layered.
- ADR 0019: Read-Write Trino Instance for Transformer-Issued View DDL — establishes the dual-Trino topology; clarifies that authorization applies only to the user-facing Trino.
- ADR 0021: OPA User Attribute Distribution via MinIO Bundles — how `data.users` reaches OPA.
- ADR 0022: Trino Authentication and Identity Propagation — how `input.context.identity.user` reaches the policy.
- ADR 0023: Trino View Security Model — the policy's view-related carve-outs.
- Trino docs: OPA access control — <https://trino.io/docs/current/security/opa-access-control.html>
