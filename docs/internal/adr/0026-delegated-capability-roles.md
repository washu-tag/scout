# ADR 0026: Delegated Capability Roles and the User Manager

**Date**: 2026-06-12
**Status**: Accepted
**Decision Owner**: TAG Team

## Context

The user-administration console (ADR 0025) is gated entirely on `scout-admin` —
and `scout-admin` maps to `realm-management/realm-admin` plus the admin role in
every Scout service. There is no way to delegate day-to-day user administration
(approving access requests, editing data-access attributes) without handing over
the keys to everything. We want a **user manager** role that can run the console
but is not a realm admin and not an admin of any other Scout service, and we
expect more roles like it — each managing one aspect of one service.

A first design gated the new role with `scout-admin OR scout-user-manager`
checks in the SPI and an `isUserManager` boolean in the launchpad session. That
shape compounds badly: every future role adds another OR-branch at every
enforcement point and another session flag, and "admins can do everything a
manager can" lives as a code convention rather than a declared fact. Rejected.

## Decision

### 1. Capabilities are realm roles; groups assign them

Each delegable capability is a **Keycloak realm role** (here: `manage-users`),
assigned to groups in the realm template:

```
CAPABILITY (realm role)         ASSIGNMENT (groups, console-managed)
manage-users  ◄────────────────  scout-user-manager   (new; maps only this)
              ◄────────────────  scout-admin          (one template line)
```

Every check — the scout-users SPI, the launchpad UI — tests the **single role
name**. Admins pass because the `scout-admin` group maps the role, declared in
`scout-realm.json.j2` and reconciled by keycloak-config-cli; there is no "or
admin" logic anywhere, no migration of existing admins, and promote-to-admin is
unchanged. A future per-aspect role is one new realm role, its group mappings,
and a single-name check in the owning service. (It also becomes possible to
have admins *without* a capability by removing one mapping line.)

The `scout-user-manager` group is **additive**: members must separately be in
`scout-user`, and the group maps no service client roles — so a user manager is
not an admin of any other Scout service by construction, and OPA/Trino are
untouched (the group rides along in the OPA bundle's `groups` array, where the
rego ignores unknown groups; `approved_groups` stays `{scout-user,
scout-admin}`).

### 2. Authorization tiers and server-side invariants

The SPI (`ScoutUsersResource`) now has two tiers, with these rules enforced
server-side regardless of the UI:

1. **Capability tier** — `requireCapability("manage-users")` gates `schema`,
   `pending`, `users`, `approve`, `users/{id}/attributes`, the new
   `POST`/`DELETE users/{id}/manager` (grant/revoke the manager group), and
   offboard. The role check resolves the user's direct realm-role mappings and
   each group's role mappings **explicitly** (not `UserModel.hasRole()`), so the
   logic is deterministic and unit-testable with plain mocks; composites are
   intentionally not expanded. The `aud=scout-users-api` requirement is
   unchanged.
2. **Admin tier** — `POST`/`DELETE users/{id}/admin` (promote/demote
   `scout-admin`) keeps the live `scout-admin` group check. scout-admin is the
   protected tier and role-granting authority; referencing it here is inherent.
3. **Admin-target guard** — every capability-tier mutation (approve, set
   attributes, manager grant/revoke, offboard) rejects with 403 when the
   *target* is in `scout-admin` and the caller is not. One shared predicate
   (`requireCanModifyTarget`), not per-endpoint special cases: a manager can
   never grant/revoke `scout-admin`, offboard an admin, or rewrite an admin's
   data-access attributes.
4. **Manager ⊂ scout-user** — granting the manager role 409s unless the target
   is already in `scout-user` (a manager who isn't a scout-user would be locked
   out at the ingress holding a role that does nothing); offboard removes
   `scout-user-manager` along with the other Scout groups.
5. **No last-manager guard** — same rationale as ADR 0025's no-last-admin
   stance; admins are a strict superset and the Keycloak master console remains
   the recovery path. Manager-revokes-manager and self-revoke are allowed
   (privilege drops, symmetric with admin demote).

Manager grant/revoke fires the same `GROUP_MEMBERSHIP` admin events as
promote/demote, so OPA republish and the admin-events audit trail work
unchanged. This surfaced a latent bug: the approval-email listener matched
group events by **substring** (`representation.contains("scout-user")`), which
`"scout-user-manager"` also matches — granting the manager role would have sent
a spurious welcome email, revoking it a spurious account-disabled email. The
listener now parses the representation and compares the group name exactly.

### 3. Claims and UI

The launchpad client is `fullScopeAllowed=false`, so a realm-level
`scopeMappings` entry puts `manage-users` in its scope; the existing
`microprofile-jwt` realm-role mapper then writes it into the same `groups`
claim the client-role mapper already feeds. The session stores one filtered
`roles: string[]` (replacing the boolean-per-role pattern; `isAdmin` is derived
from it), so future capability roles add no session fields. As with `isAdmin`
(ADR 0025), roles resolve once at login: a freshly granted manager signs out/in
to see the console, and a revoked one keeps stale affordances while every SPI
call live-checks.

UI gating mirrors the server: the home page shows managers a User Management
card with only the Users tile (infrastructure consoles stay admin-only); the
console hides promote/demote-admin from non-admin callers, renders an admin's
drawer read-only for them ("admins are managed by admins"), hides the "Open in
Keycloak" link (it 403s for managers), and shows a Manager badge beside the
status badge — manager is a role marker, not a status.

## Blast radius (accepted)

**A user manager can grant themselves full unmasked data access** — self-edit
of one's own attributes is allowed (decided), so one `attributes` call sets
`allowed_facilities=*`, `mask_phi_fields=false`, `bypass_hidden_tables=true` on
their own account, and approving an account they control works the same way.
This is the same self-service property ADR 0025 accepts for stolen admin
tokens, now extended to a non-realm-admin tier: treat `scout-user-manager` as
"can see all data, audited", not as a low-trust role. What the tier *does*
remove is everything outside user administration: realm configuration, other
services' admin consoles, and any path to `scout-admin`.

## Amendments to ADR 0025

- "Every console endpoint is `scout-admin`-gated" → endpoints are now
  capability-gated as above; the two admin endpoints remain scout-admin-gated.
- The proxy forwards the new `users/{id}/manager` paths; its allowlist note
  ("Keycloak still enforces scout-admin on every endpoint") is updated to match.
- A fresh realm is still console-locked: until the first `scout-admin` is
  seeded in the Keycloak master console, no one holds `manage-users` either.

## Consequences

- Day-to-day user administration is delegable without realm-admin, and the
  delegation is itself delegable (managers can make managers) while capped
  below `scout-admin`.
- The role-scaling pattern is set: capability realm role + group mappings +
  single-name checks. Adding "manage-`<aspect>`" for another service touches the
  realm template, the owning service's gate, and nothing else.
- Introducing the template's first `roles.realm` section interacts with
  keycloak-config-cli's full-reconciliation behavior; verified against a dev
  cluster before rollout (fallback documented: `import.managed.role: no-delete`
  in the config-cli values).
- User managers are the first non-realm-admin principals to emit Keycloak admin
  events through the SPI's `AdminEventBuilder`; verified end-to-end that a
  manager's approve lands in the admin-events log with the manager as actor.

## References

- [ADR 0025](0025-launchpad-user-approval.md) (the console this delegates),
  ADR 0020 (attribute-driven Trino AuthZ), [ADR 0021](0021-opa-user-attribute-distribution.md)
  (OPA bundle publisher / admin events)
