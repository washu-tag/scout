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

async function globalSetup(): Promise<void> {
  console.log('\n--- Global Setup ---');

  const hostname = process.env.SCOUT_HOSTNAME;
  if (!hostname) throw new Error('SCOUT_HOSTNAME is not set');

  const password = process.env.TEST_USER_PASSWORD;
  if (!password) throw new Error('TEST_USER_PASSWORD is not set');

  // Ensure .auth directory exists
  fs.mkdirSync(AUTH_DIR, { recursive: true });

  const keycloak = new KeycloakAdmin();

  // Disable the IdP auto-redirect so tests can use the Keycloak login form
  // directly with username/password instead of being redirected to GitHub/Microsoft.
  console.log('Disabling IdP auto-redirect in Keycloak browser flow');
  await keycloak.disableIdpRedirect();

  const users: TestUserRecord[] = [];

  // --- Unauthorized user (no group membership) ---
  const unauthorizedUsername = process.env.UNAUTHORIZED_USER_USERNAME;
  if (!unauthorizedUsername) throw new Error('UNAUTHORIZED_USER_USERNAME is not set');

  const unauthorizedId = await keycloak.createUser({
    username: unauthorizedUsername,
    password,
    email: process.env.UNAUTHORIZED_USER_EMAIL ?? `${unauthorizedUsername}@example.com`,
    firstName: process.env.UNAUTHORIZED_USER_FIRST_NAME ?? 'Unauthorized',
    lastName: process.env.UNAUTHORIZED_USER_LAST_NAME ?? 'TestUser',
  });
  users.push({ id: unauthorizedId, username: unauthorizedUsername });

  // --- Authorized user (scout-user group) ---
  const authorizedUsername = process.env.AUTHORIZED_USER_USERNAME;
  if (!authorizedUsername) throw new Error('AUTHORIZED_USER_USERNAME is not set');

  const authorizedId = await keycloak.createUser({
    username: authorizedUsername,
    password,
    email: process.env.AUTHORIZED_USER_EMAIL ?? `${authorizedUsername}@example.com`,
    firstName: process.env.AUTHORIZED_USER_FIRST_NAME ?? 'Authorized',
    lastName: process.env.AUTHORIZED_USER_LAST_NAME ?? 'TestUser',
    groups: ['scout-user'],
  });
  users.push({ id: authorizedId, username: authorizedUsername });

  // Save user records for teardown
  fs.writeFileSync(USERS_FILE, JSON.stringify(users, null, 2), 'utf-8');
  console.log(`Saved ${users.length} user records to ${USERS_FILE}`);

  console.log('--- Setup Complete ---\n');
}

export default globalSetup;
