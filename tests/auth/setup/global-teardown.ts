import fs from 'fs';
import path from 'path';
import dotenv from 'dotenv';
import { KeycloakAdmin } from '../helpers/keycloak-admin';

dotenv.config({ path: path.resolve(__dirname, '..', '.env') });

const AUTH_DIR = path.resolve(__dirname, '..', '.auth');
const USERS_FILE = path.join(AUTH_DIR, 'test-users.json');

interface TestUserRecord {
  id: string;
  username: string;
}

async function globalTeardown(): Promise<void> {
  console.log('\n--- Global Teardown ---');

  const keycloak = new KeycloakAdmin();

  // Re-enable the IdP auto-redirect that was disabled during setup
  try {
    console.log('Re-enabling IdP auto-redirect in Keycloak browser flow');
    await keycloak.enableIdpRedirect();
  } catch (err) {
    console.error('Warning: failed to re-enable IdP redirect:', err);
  }

  // Strip credentials and group membership from test users (but keep the
  // users themselves so Grafana's cached user records don't conflict on
  // the next run when Keycloak assigns new UUIDs).
  if (fs.existsSync(USERS_FILE)) {
    const records: TestUserRecord[] = JSON.parse(fs.readFileSync(USERS_FILE, 'utf-8'));
    const authorizedUsername = process.env.AUTHORIZED_USER_USERNAME ?? 'scout-authorized-test-user';

    for (const { id, username } of records) {
      try {
        console.log(`Removing credentials for test user "${username}" (${id})`);
        await keycloak.removeUserCredentials(id);
      } catch (err) {
        console.error(`Warning: failed to remove credentials for "${username}":`, err);
      }

      // Revoke scout-user group from the authorized user
      if (username === authorizedUsername) {
        try {
          const groupId = await keycloak.getGroupByName('scout-user');
          console.log(`Removing "${username}" from scout-user group`);
          await keycloak.removeUserFromGroup(id, groupId);
        } catch (err) {
          console.error(`Warning: failed to remove "${username}" from group:`, err);
        }
      }
    }
  }

  // Clean up .auth directory
  if (fs.existsSync(AUTH_DIR)) {
    fs.rmSync(AUTH_DIR, { recursive: true });
    console.log('Cleaned up .auth/ directory');
  }

  console.log('--- Teardown Complete ---\n');
}

export default globalTeardown;
