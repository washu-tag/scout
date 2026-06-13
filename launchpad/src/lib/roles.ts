// Scout role names the launchpad UI gates on. Capability realm roles (ADR 0026)
// arrive in the same Keycloak `groups` claim as the launchpad client roles; the
// session keeps only this filtered set, so adding a capability means adding its
// name here and gating on it — no new session fields.
export const ADMIN_ROLE = 'launchpad-admin';
export const MANAGE_USERS_ROLE = 'manage-users';

const UI_ROLES = new Set([ADMIN_ROLE, MANAGE_USERS_ROLE]);

/** The subset of a Keycloak groups claim the UI cares about. */
export function uiRoles(groups?: string[]): string[] {
  return (groups ?? []).filter((g) => UI_ROLES.has(g));
}

export function canManageUsers(roles?: string[]): boolean {
  return (roles ?? []).includes(MANAGE_USERS_ROLE);
}
