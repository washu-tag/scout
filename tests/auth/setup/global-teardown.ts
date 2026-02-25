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
  } catch {
    console.error('Warning: failed to re-enable IdP redirect');
  }

  // Strip credentials and group membership from test users (but keep the
  // users themselves so old user records don't conflict on the next run).
  const testUsernames = [
    process.env.UNAUTHORIZED_USER_USERNAME,
    process.env.AUTHORIZED_USER_USERNAME,
  ].filter(Boolean) as string[];

  console.log('Cleaning up test users by removing credentials and group memberships');
  for (const username of testUsernames) {
    try {
      await keycloak.cleanupUser(username);
    } catch {
      console.error('Warning: failed to clean up test user');
    }
  }

  console.log('--- Teardown Complete ---\n');
}

export default globalTeardown;
