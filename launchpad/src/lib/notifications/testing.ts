import { randomUUID } from 'node:crypto';
import {
  createLocalJWKSet,
  exportJWK,
  generateKeyPair,
  SignJWT,
  type CryptoKey,
  type JWTVerifyGetKey,
} from 'jose';
import { Pool } from 'pg';
import { createPool, migrate } from './db';
import type { Sender } from './registration';
import { SEND_ROLE } from './token';

// Tests that need Postgres run when LAUNCHPAD_TEST_DATABASE_URL names one, each
// file in a schema of its own.
const url = process.env.LAUNCHPAD_TEST_DATABASE_URL;

export const needsDatabase = url ? false : 'LAUNCHPAD_TEST_DATABASE_URL is not set';

export async function testDatabase(): Promise<{
  pool: Pool;
  url: string;
  schema: string;
  drop: () => Promise<void>;
}> {
  const schema = `test_${randomUUID().replaceAll('-', '')}`;
  const admin = new Pool({ connectionString: url });
  await admin.query(`CREATE SCHEMA ${schema}`);
  const scoped = new URL(url!);
  scoped.searchParams.set('options', `-c search_path=${schema}`);
  scoped.searchParams.set('application_name', schema);
  const pool = createPool(scoped.href);
  await migrate(pool);
  return {
    pool,
    url: scoped.href,
    schema,
    drop: async () => {
      await pool.end();
      await admin.query(`DROP SCHEMA ${schema} CASCADE`);
      await admin.end();
    },
  };
}

export const SENDER: Sender = {
  clientId: 'request-tracker',
  displayName: 'Requests',
  link: { subdomain: 'requests', pathPrefixes: ['/requests', '/review/'] },
  categories: ['review-needed', 'job-finished'],
  source: 'test/senders.yaml',
};

export const ISSUER = 'https://keycloak.scout.example.edu/realms/scout';
export const LAUNCHPAD = 'launchpad';

export interface Realm {
  keys: JWTVerifyGetKey;
  token: (
    claims?: Record<string, unknown>,
    options?: { expiresIn?: string; key?: CryptoKey },
  ) => Promise<string>;
  otherKey: CryptoKey;
}

// A stand-in realm: tokens shaped like Keycloak's client-credentials tokens.
export async function testRealm(): Promise<Realm> {
  const { publicKey, privateKey } = await generateKeyPair('RS256');
  const other = await generateKeyPair('RS256');
  const jwk = { ...(await exportJWK(publicKey)), kid: 'realm-key', alg: 'RS256', use: 'sig' };
  return {
    keys: createLocalJWKSet({ keys: [jwk] }),
    otherKey: other.privateKey,
    token: (claims = {}, { expiresIn = '5m', key = privateKey } = {}) =>
      new SignJWT({
        iss: ISSUER,
        aud: LAUNCHPAD,
        typ: 'Bearer',
        azp: 'request-tracker',
        resource_access: { [LAUNCHPAD]: { roles: [SEND_ROLE] } },
        ...claims,
      })
        .setProtectedHeader({ alg: 'RS256', kid: 'realm-key', typ: 'JWT' })
        .setIssuedAt()
        .setExpirationTime(expiresIn)
        .sign(key),
  };
}
