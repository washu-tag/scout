/**
 * Keycloak Admin API helper.
 *
 * Uses native fetch (Node 18+) to manage test users in the Scout realm
 * via the Keycloak Admin REST API.
 */

interface UserConfig {
  username: string;
  password: string;
  email: string;
  firstName: string;
  lastName: string;
  groups?: string[];
}

export class KeycloakAdmin {
  private readonly baseUrl: string;
  private readonly adminUser: string;
  private readonly adminPassword: string;
  private accessToken: string | null = null;

  constructor() {
    const hostname = process.env.SCOUT_HOSTNAME;
    if (!hostname) throw new Error('SCOUT_HOSTNAME is not set');

    this.baseUrl = `https://keycloak.${hostname}`;
    this.adminUser = process.env.KEYCLOAK_ADMIN_USER ?? 'admin';
    this.adminPassword = process.env.KEYCLOAK_ADMIN_PASSWORD ?? '';

    if (!this.adminPassword) {
      throw new Error('KEYCLOAK_ADMIN_PASSWORD is not set');
    }
  }

  /** Obtain an admin access token from the master realm. */
  async authenticate(): Promise<void> {
    const url = `${this.baseUrl}/realms/master/protocol/openid-connect/token`;
    const body = new URLSearchParams({
      grant_type: 'password',
      client_id: 'admin-cli',
      username: this.adminUser,
      password: this.adminPassword,
    });

    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Keycloak admin auth failed (${res.status}): ${text}`);
    }

    const json = (await res.json()) as { access_token: string };
    this.accessToken = json.access_token;
  }

  /** Create a user in the Scout realm. Returns the new user's ID. */
  async createUser(config: UserConfig): Promise<string> {
    await this.ensureAuthenticated();

    const url = `${this.baseUrl}/admin/realms/scout/users`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${this.accessToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        username: config.username,
        email: config.email,
        firstName: config.firstName,
        lastName: config.lastName,
        enabled: true,
        emailVerified: true,
        credentials: [
          {
            type: 'password',
            value: config.password,
            temporary: false,
          },
        ],
      }),
    });

    if (res.status === 409) {
      // User already exists — look up and return their ID
      console.log(`User "${config.username}" already exists, reusing.`);
      return this.getUserByUsername(config.username);
    }

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Failed to create user (${res.status}): ${text}`);
    }

    // Extract user ID from the Location header
    const location = res.headers.get('location');
    if (!location) {
      // Fall back to lookup
      return this.getUserByUsername(config.username);
    }

    const userId = location.split('/').pop()!;
    console.log(`Created user "${config.username}" (${userId})`);

    // Assign groups if specified
    if (config.groups?.length) {
      for (const groupName of config.groups) {
        const groupId = await this.getGroupByName(groupName);
        await this.addUserToGroup(userId, groupId);
      }
    }

    return userId;
  }

  /** Look up a user by exact username. Returns the user ID. */
  async getUserByUsername(username: string): Promise<string> {
    await this.ensureAuthenticated();

    const url = `${this.baseUrl}/admin/realms/scout/users?username=${encodeURIComponent(username)}&exact=true`;
    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${this.accessToken}` },
    });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Failed to look up user (${res.status}): ${text}`);
    }

    const users = (await res.json()) as { id: string }[];
    if (users.length === 0) {
      throw new Error(`User "${username}" not found`);
    }

    return users[0].id;
  }

  /** Delete a user by ID. */
  async deleteUser(userId: string): Promise<void> {
    await this.ensureAuthenticated();

    const url = `${this.baseUrl}/admin/realms/scout/users/${userId}`;
    const res = await fetch(url, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${this.accessToken}` },
    });

    if (res.status === 404) {
      console.log(`User ${userId} already deleted.`);
      return;
    }

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Failed to delete user (${res.status}): ${text}`);
    }

    console.log(`Deleted user ${userId}`);
  }

  /** Delete a user by username (idempotent — no error if user doesn't exist). */
  async deleteUserByUsername(username: string): Promise<void> {
    await this.ensureAuthenticated();

    try {
      const userId = await this.getUserByUsername(username);
      await this.deleteUser(userId);
    } catch (err) {
      if (err instanceof Error && err.message.includes('not found')) {
        console.log(`User "${username}" not found — nothing to delete.`);
        return;
      }
      throw err;
    }
  }

  /** Look up a group by exact name. Returns the group ID. */
  async getGroupByName(name: string): Promise<string> {
    await this.ensureAuthenticated();

    const url = `${this.baseUrl}/admin/realms/scout/groups?search=${encodeURIComponent(name)}&exact=true`;
    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${this.accessToken}` },
    });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Failed to look up group (${res.status}): ${text}`);
    }

    const groups = (await res.json()) as { id: string; name: string }[];
    if (groups.length === 0) {
      throw new Error(`Group "${name}" not found`);
    }

    return groups[0].id;
  }

  /** Add a user to a group. */
  async addUserToGroup(userId: string, groupId: string): Promise<void> {
    await this.ensureAuthenticated();

    const url = `${this.baseUrl}/admin/realms/scout/users/${userId}/groups/${groupId}`;
    const res = await fetch(url, {
      method: 'PUT',
      headers: { Authorization: `Bearer ${this.accessToken}` },
    });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Failed to add user ${userId} to group ${groupId} (${res.status}): ${text}`);
    }

    console.log(`Added user ${userId} to group ${groupId}`);
  }

  private async ensureAuthenticated(): Promise<void> {
    if (!this.accessToken) {
      await this.authenticate();
    }
  }
}
