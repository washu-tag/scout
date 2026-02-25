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
    const url = `${this.baseUrl}/admin/realms/scout/users`;
    const res = await this.fetchWithAuth(url, {
      method: 'POST',
      headers: {
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
      // 409 can mean duplicate username OR duplicate email.
      console.log('User already exists, reusing.');
      let userId: string;
      try {
        userId = await this.getUserByUsername(config.username);
      } catch {
        throw new Error(
          `409 Conflict creating user "${config.username}" but no user with that username exists. ` +
            `Another Keycloak user likely has the email "${config.email}". ` +
            `Ensure test user emails are unique across the realm.`,
        );
      }

      await this.resetUserPassword(userId, config.password);

      if (config.groups?.length) {
        for (const groupName of config.groups) {
          const groupId = await this.getGroupByName(groupName);
          await this.addUserToGroup(userId, groupId);
        }
      }

      return userId;
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
    console.log('Created user');

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
    const url = `${this.baseUrl}/admin/realms/scout/users?username=${encodeURIComponent(username)}&exact=true`;
    const res = await this.fetchWithAuth(url);

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

  /** Look up a group by exact name. Returns the group ID. */
  async getGroupByName(name: string): Promise<string> {
    const url = `${this.baseUrl}/admin/realms/scout/groups?search=${encodeURIComponent(name)}&exact=true`;
    const res = await this.fetchWithAuth(url);

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
    const url = `${this.baseUrl}/admin/realms/scout/users/${userId}/groups/${groupId}`;
    const res = await this.fetchWithAuth(url, { method: 'PUT' });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Failed to add user ${userId} to group ${groupId} (${res.status}): ${text}`);
    }

    console.log('Added user to group');
  }

  /** Reset a user's password. */
  async resetUserPassword(userId: string, password: string): Promise<void> {
    const url = `${this.baseUrl}/admin/realms/scout/users/${userId}/reset-password`;
    const res = await this.fetchWithAuth(url, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        type: 'password',
        value: password,
        temporary: false,
      }),
    });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Failed to reset password for user ${userId} (${res.status}): ${text}`);
    }

    console.log('Reset password for user');
  }

  /** Remove all credentials from a user. */
  async removeUserCredentials(userId: string): Promise<void> {
    const listUrl = `${this.baseUrl}/admin/realms/scout/users/${userId}/credentials`;
    const listRes = await this.fetchWithAuth(listUrl);

    if (!listRes.ok) {
      const text = await listRes.text();
      throw new Error(`Failed to list credentials for user ${userId} (${listRes.status}): ${text}`);
    }

    const credentials = (await listRes.json()) as { id: string; type: string }[];

    for (const cred of credentials) {
      const delUrl = `${this.baseUrl}/admin/realms/scout/users/${userId}/credentials/${cred.id}`;
      const delRes = await this.fetchWithAuth(delUrl, { method: 'DELETE' });

      if (!delRes.ok) {
        const text = await delRes.text();
        throw new Error(
          `Failed to delete credential ${cred.id} for user ${userId} (${delRes.status}): ${text}`,
        );
      }

      console.log('Removed credential from user');
    }
  }

  /** Get all groups a user belongs to. */
  async getUserGroups(userId: string): Promise<{ id: string; name: string }[]> {
    const url = `${this.baseUrl}/admin/realms/scout/users/${userId}/groups`;
    const res = await this.fetchWithAuth(url);

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Failed to get groups for user ${userId} (${res.status}): ${text}`);
    }

    return (await res.json()) as { id: string; name: string }[];
  }

  /** Remove credentials and group memberships from a user. */
  async cleanupUser(username: string): Promise<void> {
    const userId = await this.getUserByUsername(username);
    await this.removeUserCredentials(userId);
    const groups = await this.getUserGroups(userId);
    for (const group of groups) {
      await this.removeUserFromGroup(userId, group.id);
    }
  }

  /** Remove a user from a group. */
  async removeUserFromGroup(userId: string, groupId: string): Promise<void> {
    const url = `${this.baseUrl}/admin/realms/scout/users/${userId}/groups/${groupId}`;
    const res = await this.fetchWithAuth(url, { method: 'DELETE' });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(
        `Failed to remove user ${userId} from group ${groupId} (${res.status}): ${text}`,
      );
    }

    console.log('Removed user from group');
  }

  /**
   * Disable the identity-provider-redirector execution in the browser
   * authentication flow so Keycloak shows the login form instead of
   * auto-redirecting to the default IdP (e.g. GitHub).
   */
  async disableIdpRedirect(): Promise<void> {
    await this.setIdpRedirectRequirement('DISABLED');
  }

  /**
   * Re-enable the identity-provider-redirector execution in the browser
   * authentication flow (restores auto-redirect to the default IdP).
   */
  async enableIdpRedirect(): Promise<void> {
    await this.setIdpRedirectRequirement('ALTERNATIVE');
  }

  private async setIdpRedirectRequirement(requirement: string): Promise<void> {
    // Get all executions in the browser flow
    const execUrl = `${this.baseUrl}/admin/realms/scout/authentication/flows/browser/executions`;
    const res = await this.fetchWithAuth(execUrl);

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Failed to get browser flow executions (${res.status}): ${text}`);
    }

    const executions = (await res.json()) as {
      id: string;
      providerId: string;
      requirement: string;
    }[];
    const idpExecution = executions.find((e) => e.providerId === 'identity-provider-redirector');

    if (!idpExecution) {
      throw new Error('identity-provider-redirector execution not found in browser flow');
    }

    if (idpExecution.requirement === requirement) {
      console.log(`identity-provider-redirector already ${requirement}`);
      return;
    }

    // Update the execution requirement
    const updateRes = await this.fetchWithAuth(execUrl, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ...idpExecution, requirement }),
    });

    if (!updateRes.ok) {
      const text = await updateRes.text();
      throw new Error(
        `Failed to update identity-provider-redirector (${updateRes.status}): ${text}`,
      );
    }

    console.log(`Set identity-provider-redirector to ${requirement}`);
  }

  private async ensureAuthenticated(): Promise<void> {
    if (!this.accessToken) {
      await this.authenticate();
    }
  }

  /**
   * Authenticated fetch with automatic 401 retry.
   * Ensures a valid token, makes the request, and if the response is 401,
   * re-authenticates and retries once to handle token expiry transparently.
   */
  private async fetchWithAuth(url: string, options: RequestInit = {}): Promise<Response> {
    await this.ensureAuthenticated();

    const makeRequest = () =>
      fetch(url, {
        ...options,
        headers: {
          ...options.headers,
          Authorization: `Bearer ${this.accessToken}`,
        },
      });

    const res = await makeRequest();

    if (res.status === 401) {
      await this.authenticate();
      return makeRequest();
    }

    return res;
  }
}
