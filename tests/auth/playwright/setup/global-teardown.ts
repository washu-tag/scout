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

  // Delete test users — try saved JSON first, fall back to username lookup
  if (fs.existsSync(USERS_FILE)) {
    const records: TestUserRecord[] = JSON.parse(fs.readFileSync(USERS_FILE, 'utf-8'));

    for (const { id, username } of records) {
      try {
        console.log(`Deleting test user "${username}" (${id})`);
        await keycloak.deleteUser(id);
      } catch (err) {
        console.error(`Warning: failed to delete user "${username}":`, err);
      }
    }
  } else {
    // Fallback: delete by username
    const usernames = [
      process.env.UNAUTHORIZED_USER_USERNAME ?? 'scout-unauthorized-test-user',
      process.env.AUTHORIZED_USER_USERNAME ?? 'scout-authorized-test-user',
    ];

    for (const username of usernames) {
      try {
        console.log(`No saved user ID — deleting by username: ${username}`);
        await keycloak.deleteUserByUsername(username);
      } catch (err) {
        console.error(`Warning: failed to delete user "${username}":`, err);
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
