import path from 'path';
import dotenv from 'dotenv';
import { KeycloakAdmin } from '../helpers/keycloak-admin';

dotenv.config({ path: path.resolve(__dirname, '..', '.env') });

async function globalSetup(): Promise<void> {
  console.log('\n--- Global Setup ---');

  const hostname = process.env.SCOUT_HOSTNAME;
  if (!hostname) throw new Error('SCOUT_HOSTNAME is not set');

  const password = process.env.TEST_USER_PASSWORD;
  if (!password) throw new Error('TEST_USER_PASSWORD is not set');

  const keycloak = new KeycloakAdmin();

  // Disable the IdP auto-redirect so tests can use the Keycloak login form
  // directly with username/password instead of being redirected to GitHub/Microsoft.
  console.log('Disabling IdP auto-redirect in Keycloak browser flow');
  await keycloak.disableIdpRedirect();

  // Clean up stale state from any previous failed teardown so tests start
  // from a known baseline. If the users don't exist yet this is a no-op.
  const testUsernames = [
    process.env.UNAUTHORIZED_USER_USERNAME,
    process.env.AUTHORIZED_USER_USERNAME,
  ].filter(Boolean) as string[];

  console.log('Cleaning up stale state for test users if they already exist');
  for (const username of testUsernames) {
    try {
      await keycloak.cleanupUser(username);
    } catch {
      // User doesn't exist yet, nothing to clean up
    }
  }

  // --- Unauthorized user (no group membership) ---
  const unauthorizedUsername = process.env.UNAUTHORIZED_USER_USERNAME;
  if (!unauthorizedUsername) throw new Error('UNAUTHORIZED_USER_USERNAME is not set');

  console.log('Creating unauthorized test user');
  await keycloak.createUser({
    username: unauthorizedUsername,
    password,
    email: process.env.UNAUTHORIZED_USER_EMAIL ?? `${unauthorizedUsername}@example.com`,
    firstName: process.env.UNAUTHORIZED_USER_FIRST_NAME ?? 'Unauthorized',
    lastName: process.env.UNAUTHORIZED_USER_LAST_NAME ?? 'TestUser',
  });

  // --- Authorized user (scout-user group) ---
  const authorizedUsername = process.env.AUTHORIZED_USER_USERNAME;
  if (!authorizedUsername) throw new Error('AUTHORIZED_USER_USERNAME is not set');

  console.log('Creating authorized test user');
  await keycloak.createUser({
    username: authorizedUsername,
    password,
    email: process.env.AUTHORIZED_USER_EMAIL ?? `${authorizedUsername}@example.com`,
    firstName: process.env.AUTHORIZED_USER_FIRST_NAME ?? 'Authorized',
    lastName: process.env.AUTHORIZED_USER_LAST_NAME ?? 'TestUser',
    groups: ['scout-user'],
  });

  console.log('--- Setup Complete ---\n');
}

export default globalSetup;
