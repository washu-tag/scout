# ADR 0023: Trino View Security Model

**Date**: 2026-05-19
**Status**: Accepted
**Decision Owner**: TAG Team

## Context

Scout's silver layer includes a family of views (the `_epic_view` views: `reports_epic_view`, `reports_curated_epic_view`, `reports_latest_epic_view`, `reports_dx_epic_view`) that join the report tables to `reports_report_patient_mapping` to resolve Epic MRNs. These views are created by `hl7-transformer` against `trino-rw` (ADR 0019) and queried by users against the user-facing Trino under [ADR 0020](0020-trino-rbac-architecture.md)'s OPA policy.

Two policy interactions make views non-trivial:

- **The join target (`reports_report_patient_mapping`) lacks `sending_facility`.** It can't be row-filtered the same way as the report tables. A facility-restricted user querying it directly could enumerate every patient identifier in the system.
- **Column masks on the join target's columns** (`m.mpi`, `m.epic_mrn`) would propagate through the views' window functions (`MAX(m.epic_mrn) OVER (PARTITION BY ...)`) and NULL out `resolved_epic_mrn` for every user — defeating the views' point.

This ADR specifies the view-security choices that make both work. The naïvely intuitive answer (`SECURITY INVOKER`, so the user's RBAC applies) doesn't.

## Decision

**Views are created with `SECURITY DEFINER` (Trino's CREATE VIEW default). The OPA policy carves out three pieces to make this work:**

1. **`view_owner_principals`** — a data set whose members (e.g. `trino`, the user `hl7-transformer` creates views as) bypass row-filter and column-mask evaluation. Without this, the view owner's underlying-table reads would be clamped to zero rows by the facility filter.
2. **`view_only_tables`** — an inventory-driven list of tables that are denied for direct SELECT and hidden from `SHOW TABLES`. Includes `reports_report_patient_mapping` and similar join-target tables that lack a filterable column.
3. **`CreateViewWithSelectFromColumns`** allow rule — exempts the view owner from `view_only_blocked`, so DEFINER views' internal joins to `view_only_tables` succeed while direct invoker queries against those same tables are denied.

The invoker's RBAC is still enforced because the views themselves are listed in `data.filtered_tables`. Row filters apply to the view's output (post-DEFINER materialization), not to the view owner's underlying reads.

### Trade-offs in one paragraph

`SECURITY INVOKER` would be the natural-intuition choice — "let the invoker's permissions apply." It fails for two reasons. First, the `view_only_tables` deny on `reports_report_patient_mapping` propagates: the invoker can't query the mapping table directly *and* can't query a view that joins to it. Second, the column masks on `m.mpi`/`m.epic_mrn` propagate through the window function: every user's `resolved_epic_mrn` resolves to NULL because the underlying value got masked before the `MAX(...) OVER (...)` ran. DEFINER preserves both behaviors because the view owner sees raw rows from the join target *during materialization*, and the invoker's RBAC applies to the resulting view rows.

## Alternatives Considered

### `SECURITY INVOKER` for views

Invoker's permissions apply to all underlying reads.

**Rejected**: described above. Propagates `view_only_tables` deny to the view (breaking it for every user) and propagates column masks through window functions (NULLing out `resolved_epic_mrn`).

### Materialized views / scheduled snapshot tables

Materialize the `_epic_view` results into ordinary tables (refreshed on a schedule). Apply the same OPA row-filter / column-mask treatment to the snapshot tables.

**Rejected**: introduces a freshness lag (the snapshot is by definition stale relative to the source). The HL7 ingest pipeline already runs continuously; users expect Epic resolution to be live. A materialization step adds a dependency between the view's freshness and the snapshot schedule, plus storage cost. The DEFINER + carve-out approach gets the same answer without the latency.

### Application-level join instead of a view

Drop the views. Have each client (Superset, notebooks, MCP) issue a multi-statement query that selects from `reports*` and joins to `reports_report_patient_mapping` in a second statement, with the mapping read scoped via a different mechanism (a Scout-side rewriter, or an admin-issued one-shot query).

**Rejected**: pushes complexity into every client. The MCP can't do multi-statement reasoning over the policy easily; Superset SQL Lab users would have to write the join by hand; notebooks would each need a Scout-specific helper. The view is the right place to encapsulate the join, and DEFINER + carve-outs makes it work under RBAC.

### Per-user precomputed views

Generate a `_epic_view_<user>` for each user with the join already filtered by their `allowed_facilities`.

**Rejected**: combinatorial. Re-create on every Keycloak attribute change. Doesn't compose with the `allowed_modalities` (or future) dimension without exponential blowup. Defeats the "adding a dimension is one inventory edit" property of ADR 0020.

## Consequences

### Positive

- **The `_epic_view` family works under RBAC.** Users see Epic MRN resolution joined into their facility-scoped view rows. Direct queries against the mapping table fail (as intended).
- **The policy data model is extensible.** `view_only_tables` is inventory-driven — adding another join target table to the deny list is one inventory edit, not a Rego change.
- **Column masks compose naturally.** Masks on report-table columns apply to view output (the invoker is the principal); masks on mapping-table columns don't propagate through the window function (the view owner reads raw rows).

### Negative

- **The DEFINER model is counterintuitive.** A future maintainer reading the OPA policy will see the `view_owner_principals` exemption, the `view_only_tables` deny, and the `CreateViewWithSelectFromColumns` allow rule and need this ADR to understand why all three exist. Without context, the policy looks like it's bypassing its own RBAC.
- **The view owner has effective superuser read access.** Any pod that can authenticate to Trino as `trino` (currently only `hl7-transformer` via `trino-rw`) can read the mapping table in full. This is the cost of DEFINER and the reason `trino-rw` access is gated by NetworkPolicy in ADR 0019.

## Implementation Notes

- **View creation**: `hl7-transformer` issues `CREATE OR REPLACE VIEW … SECURITY DEFINER` against `trino-rw` (the only Trino instance that permits writes; per ADR 0019). The view's owner is `trino`, which is what the OPA policy uses as the `view_owner_principals` member.
- **`view_owner_principals`** is inventory-driven (`trino_view_owner_principals`) and rendered into the OPA data document by the opa Ansible role. Default: `["trino"]`. Membership is checked in the row-filter and column-mask rules via the `is_view_owner` helper.
- **`view_only_tables`** is inventory-driven (`trino_view_only_tables`) and rendered the same way. Default includes the patient-identifier join targets. Tables in the list are:
  - Denied for direct SELECT in the table-level `allow` rule (`view_only_blocked` check).
  - Hidden from `SHOW TABLES` by the same check.
  - Reachable via DEFINER views thanks to the `CreateViewWithSelectFromColumns` allow rule that exempts the view owner from `view_only_blocked`.
- **Column-mask type dispatch**: the policy emits `'[REDACTED]'` (varchar) or bare `NULL` (every other type) based on the column's declared SQL type. Bare `NULL` coerces cleanly even for complex types like `array(row(...))`, so no per-type `CAST` is needed.

## References

- ADR 0019: Read-Write Trino Instance for Transformer-Issued View DDL — explains why `trino-rw` is the only path for `CREATE VIEW` and why its NetworkPolicy limits view-owner-credential exposure.
- ADR 0020: Trino Authorization via OPA with Keycloak Attributes — the broader policy engine this ADR's carve-outs plug into.
- ADR 0022: Trino Authentication and Identity Propagation — explains how `input.context.identity.user` reaches the policy for the invoker side of the DEFINER trade.
- Trino docs: CREATE VIEW — <https://trino.io/docs/current/sql/create-view.html>
