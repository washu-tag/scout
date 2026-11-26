import { z } from 'zod';
import { tokenRequest } from '@/lib/keycloakToken';

// Expands a sender's client role to the users who hold it now (ADR 0043).
//
// The Keycloak side is the scout-users resource (ADR 0025):
//
//   GET {issuer}/scout-users/clients/{clientId}/roles/{role}/members?max={n}
//   Authorization: Bearer <the launchpad client's client-credentials token>
//
//   200 {"usernames": ["alice", "bob"], "truncated": false}
//   404 {"error": "role_not_found"}   no such client, or no such role on it
//   401 / 403                         the caller isn't the launchpad's client
//
// `usernames` are the enabled users holding the role directly, through a
// group, or through a composite role, each once, at most `max` of them;
// `truncated` says the role has more.

export interface RoleMembers {
  usernames: string[];
  truncated: boolean;
}

export interface RoleDirectory {
  // null when the client has no such role.
  members(clientId: string, role: string, max: number): Promise<RoleMembers | null>;
}

export class RoleLookupError extends Error {}

const membersSchema = z.object({ usernames: z.array(z.string()), truncated: z.boolean() });

export function keycloakRoleDirectory(issuer: string): RoleDirectory {
  let cached: { token: string; until: number } | null = null;

  async function token(): Promise<string> {
    if (cached && cached.until > Date.now()) return cached.token;
    const response = await tokenRequest('token', { grant_type: 'client_credentials' });
    const data = (await response.json().catch(() => ({}))) as {
      access_token?: string;
      expires_in?: number;
    };
    if (!response.ok || !data.access_token) {
      throw new RoleLookupError(`client-credentials grant failed (${response.status})`);
    }
    cached = {
      token: data.access_token,
      until: Date.now() + Math.max(0, (data.expires_in ?? 60) - 30) * 1000,
    };
    return cached.token;
  }

  return {
    async members(clientId, role, max) {
      const url = `${issuer}/scout-users/clients/${encodeURIComponent(clientId)}/roles/${encodeURIComponent(role)}/members?max=${max}`;
      const response = await fetch(url, { headers: { Authorization: `Bearer ${await token()}` } });
      const body = await response.json().catch(() => null);
      if (response.status === 404 && body?.error === 'role_not_found') return null;
      if (!response.ok) throw new RoleLookupError(`role lookup failed (${response.status})`);
      const parsed = membersSchema.safeParse(body);
      if (!parsed.success) throw new RoleLookupError('role lookup answered in an unknown shape');
      return {
        usernames: parsed.data.usernames.map((name) => name.toLowerCase()),
        truncated: parsed.data.truncated,
      };
    },
  };
}

// Test double: members keyed by "<clientId>/<role>".
export function staticRoleDirectory(members: Record<string, string[]>): RoleDirectory {
  return {
    async members(clientId, role, max) {
      const all = members[`${clientId}/${role}`];
      if (!all) return null;
      return { usernames: all.slice(0, max), truncated: all.length > max };
    },
  };
}
