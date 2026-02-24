import path from 'path';
import dotenv from 'dotenv';
import { KeycloakAdmin } from '../helpers/keycloak-admin';

dotenv.config({ path: path.resolve(__dirname, '..', '.env') });

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
  // users themselves so old user records don't conflict on the next run).
  const testUsernames = [
    process.env.UNAUTHORIZED_USER_USERNAME,
    process.env.AUTHORIZED_USER_USERNAME,
  ].filter(Boolean) as string[];

  for (const username of testUsernames) {
    try {
      const userId = await keycloak.getUserByUsername(username);
      console.log(`Cleaning up test user "${username}" (${userId})`);
      await keycloak.removeUserCredentials(userId);
      const groups = await keycloak.getUserGroups(userId);
      for (const group of groups) {
        await keycloak.removeUserFromGroup(userId, group.id);
      }
    } catch (err) {
      console.error(`Warning: failed to clean up "${username}":`, err);
    }
  }

  console.log('--- Teardown Complete ---\n');
}

export default globalTeardown;
