import { Pool } from 'pg';
import { databaseUrl } from './settings';

// Each entry runs once, in order, in one transaction with the version it brings
// the schema to. Append; never edit one that has shipped.
const MIGRATIONS = [
  `
  CREATE TABLE notices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sender text NOT NULL,
    sender_name text NOT NULL,
    category text NOT NULL,
    title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 120),
    detail text CHECK (char_length(detail) <= 300),
    link_subdomain text NOT NULL,
    link_path text NOT NULL,
    collapse_key text,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
  );
  CREATE UNIQUE INDEX notices_collapse_key ON notices (sender, collapse_key)
    WHERE collapse_key IS NOT NULL;
  CREATE INDEX notices_expires_at ON notices (expires_at);

  CREATE TABLE notice_deliveries (
    notice_id uuid NOT NULL REFERENCES notices (id) ON DELETE CASCADE,
    username text NOT NULL,
    via_role text,
    seen_at timestamptz,
    read_at timestamptz,
    archived_at timestamptz,
    PRIMARY KEY (notice_id, username)
  );
  CREATE INDEX notice_deliveries_username ON notice_deliveries (username)
    WHERE archived_at IS NULL;

  CREATE TABLE notice_idempotency_keys (
    sender text NOT NULL,
    key text NOT NULL,
    result jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sender, key)
  );

  CREATE TABLE notice_send_windows (
    sender text NOT NULL,
    window_start timestamptz NOT NULL,
    count integer NOT NULL,
    PRIMARY KEY (sender, window_start)
  );
  `,
  `
  ALTER TABLE notices
    DROP CONSTRAINT notices_detail_check,
    ADD CONSTRAINT notices_detail_check CHECK (char_length(detail) <= 1000);
  `,
];

// Replicas starting together take turns.
const MIGRATION_LOCK = 4300431;
const CLEANUP_MS = 60 * 60 * 1000;

export async function migrate(pool: Pool): Promise<void> {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    await client.query('SELECT pg_advisory_xact_lock($1)', [MIGRATION_LOCK]);
    await client.query(
      'CREATE TABLE IF NOT EXISTS launchpad_schema_migrations (version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())',
    );
    const { rows } = await client.query<{ version: number | null }>(
      'SELECT max(version) AS version FROM launchpad_schema_migrations',
    );
    for (let version = rows[0].version ?? 0; version < MIGRATIONS.length; version++) {
      await client.query(MIGRATIONS[version]);
      await client.query('INSERT INTO launchpad_schema_migrations (version) VALUES ($1)', [
        version + 1,
      ]);
    }
    await client.query('COMMIT');
  } catch (err) {
    await client.query('ROLLBACK').catch(() => {});
    throw err;
  } finally {
    client.release();
  }
}

export async function cleanup(pool: Pool): Promise<void> {
  await pool.query('DELETE FROM notices WHERE expires_at <= now()');
  await pool.query(
    "DELETE FROM notice_idempotency_keys WHERE created_at < now() - interval '24 hours'",
  );
  await pool.query(
    "DELETE FROM notice_send_windows WHERE window_start < now() - interval '1 hour'",
  );
}

export function createPool(connectionString: string): Pool {
  const pool = new Pool({
    connectionString,
    max: 10,
    connectionTimeoutMillis: 5_000,
    statement_timeout: 10_000,
  });
  // An idle client the server drops emits here; unhandled, it ends the process.
  pool.on('error', (err) => console.error('[notifications] database connection lost', err.message));
  return pool;
}

interface State {
  pool: Pool;
  migrated: boolean;
  migrating: Promise<void> | null;
  cleanup?: NodeJS.Timeout;
}

// One pool per process: Next.js can load this module once per route bundle.
const shared = globalThis as typeof globalThis & { launchpadNotifications?: State };

function state(): State {
  if (shared.launchpadNotifications) return shared.launchpadNotifications;
  const url = databaseUrl();
  if (!url) throw new Error('LAUNCHPAD_DATABASE_URL is not set');
  shared.launchpadNotifications = { pool: createPool(url), migrated: false, migrating: null };
  return shared.launchpadNotifications;
}

// The pool, once the schema is current. While the database is unreachable each
// caller fails, and the next one tries again.
export async function database(): Promise<Pool> {
  const current = state();
  if (current.migrated) return current.pool;
  current.migrating ??= migrate(current.pool)
    .then(() => {
      current.migrated = true;
    })
    .finally(() => {
      current.migrating = null;
    });
  await current.migrating;
  return current.pool;
}

export function startNotifications(): void {
  if (!databaseUrl()) return;
  database().then(
    () => console.log('[notifications] database schema is current'),
    (err) => console.error('[notifications] database migration failed', err.message),
  );
  const current = state();
  current.cleanup ??= setInterval(() => {
    database()
      .then(cleanup)
      .catch((err) => console.error('[notifications] retention cleanup failed', err.message));
  }, CLEANUP_MS);
  current.cleanup.unref();
}
