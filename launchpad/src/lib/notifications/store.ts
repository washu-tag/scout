import type { Pool, PoolClient } from 'pg';

// The channel replicas listen on. A payload names the users whose notices
// changed, never what changed.
export const CHANNEL = 'launchpad_notices';
// Under Postgres's 8000-byte payload limit.
const NOTIFY_BYTES = 7000;
const LIST_LIMIT = 100;

export interface NewNotice {
  sender: string;
  senderName: string;
  category: string;
  title: string;
  detail?: string;
  linkSubdomain: string;
  linkPath: string;
  collapseKey?: string;
  expiresAt: Date;
  // Username to the role it came through, or null when addressed by name.
  recipients: Map<string, string | null>;
}

export interface SendResult {
  id: string;
  deliveries: number;
  replaced: boolean;
}

export interface ListedNotice {
  id: string;
  sender: string;
  category: string;
  title: string;
  detail: string | null;
  link: { subdomain: string; path: string };
  createdAt: string;
  seen: boolean;
  read: boolean;
}

export interface Summary {
  unseen: number;
  unread: number;
  // Changes whenever the list's notices or their read state do.
  marker: string;
}

export type MarkAction = 'seen' | 'read' | 'archive';

export function notifyPayloads(usernames: Iterable<string>): string[] {
  const payloads: string[] = [];
  let chunk: string[] = [];
  let size = 0;
  for (const name of new Set(usernames)) {
    const cost = Buffer.byteLength(JSON.stringify(name)) + 1;
    if (chunk.length > 0 && size + cost > NOTIFY_BYTES) {
      payloads.push(JSON.stringify({ users: chunk }));
      chunk = [];
      size = 0;
    }
    chunk.push(name);
    size += cost;
  }
  if (chunk.length > 0) payloads.push(JSON.stringify({ users: chunk }));
  return payloads;
}

async function notify(client: PoolClient | Pool, usernames: Iterable<string>): Promise<void> {
  for (const payload of notifyPayloads(usernames)) {
    await client.query('SELECT pg_notify($1, $2)', [CHANNEL, payload]);
  }
}

async function transaction<T>(pool: Pool, work: (client: PoolClient) => Promise<T>): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const result = await work(client);
    await client.query('COMMIT');
    return result;
  } catch (err) {
    await client.query('ROLLBACK').catch(() => {});
    throw err;
  } finally {
    client.release();
  }
}

// Serializes sends and withdrawals of one sender's collapse key.
async function lockKey(client: PoolClient, sender: string, key: string): Promise<void> {
  await client.query('SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))', [sender, key]);
}

// Removes the sender's notice with this collapse key; the users it had reached.
async function removeCollapsed(
  client: PoolClient,
  sender: string,
  key: string,
): Promise<{ removed: boolean; usernames: string[] }> {
  const { rows } = await client.query<{ username: string }>(
    `DELETE FROM notice_deliveries WHERE notice_id IN
       (SELECT id FROM notices WHERE sender = $1 AND collapse_key = $2)
     RETURNING username`,
    [sender, key],
  );
  const deleted = await client.query(
    'DELETE FROM notices WHERE sender = $1 AND collapse_key = $2',
    [sender, key],
  );
  return { removed: (deleted.rowCount ?? 0) > 0, usernames: rows.map((row) => row.username) };
}

const VISIBLE = `
  FROM notice_deliveries d JOIN notices n ON n.id = d.notice_id
  WHERE d.username = $1 AND d.archived_at IS NULL AND n.expires_at > now()
    AND n.sender <> ALL($2::text[])`;

export function noticeStore(pool: Pool) {
  return {
    // Counts a call against the sender's limit for this minute.
    async countCall(sender: string): Promise<{ count: number; retryAfter: number }> {
      const { rows } = await pool.query<{ count: number; retry_after: number }>(
        `INSERT INTO notice_send_windows (sender, window_start, count)
         VALUES ($1, date_trunc('minute', now()), 1)
         ON CONFLICT (sender, window_start)
           DO UPDATE SET count = notice_send_windows.count + 1
         RETURNING count,
           ceil(extract(epoch FROM date_trunc('minute', now()) + interval '1 minute' - now()))::int
             AS retry_after`,
        [sender],
      );
      return { count: rows[0].count, retryAfter: Math.max(1, rows[0].retry_after) };
    },

    // The first result for an idempotency key used in the last 24 hours.
    async replay(sender: string, key: string): Promise<SendResult | null> {
      const { rows } = await pool.query<{ result: SendResult }>(
        `SELECT result FROM notice_idempotency_keys
         WHERE sender = $1 AND key = $2 AND result IS NOT NULL
           AND created_at > now() - interval '24 hours'`,
        [sender, key],
      );
      return rows[0]?.result ?? null;
    },

    async send(
      notice: NewNotice,
      idempotencyKey?: string,
    ): Promise<{ result: SendResult; replayed: boolean }> {
      return transaction(pool, async (client) => {
        if (idempotencyKey) {
          await client.query(
            `DELETE FROM notice_idempotency_keys
             WHERE sender = $1 AND key = $2 AND created_at <= now() - interval '24 hours'`,
            [notice.sender, idempotencyKey],
          );
          // A concurrent send with the same key waits here for the first to finish.
          const claim = await client.query(
            'INSERT INTO notice_idempotency_keys (sender, key) VALUES ($1, $2) ON CONFLICT DO NOTHING',
            [notice.sender, idempotencyKey],
          );
          if (claim.rowCount === 0) {
            const { rows } = await client.query<{ result: SendResult }>(
              'SELECT result FROM notice_idempotency_keys WHERE sender = $1 AND key = $2',
              [notice.sender, idempotencyKey],
            );
            return { result: rows[0].result, replayed: true };
          }
        }
        let previous = { removed: false, usernames: [] as string[] };
        if (notice.collapseKey) {
          await lockKey(client, notice.sender, notice.collapseKey);
          previous = await removeCollapsed(client, notice.sender, notice.collapseKey);
        }
        const { rows } = await client.query<{ id: string }>(
          `INSERT INTO notices (sender, sender_name, category, title, detail, link_subdomain,
             link_path, collapse_key, expires_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
           RETURNING id`,
          [
            notice.sender,
            notice.senderName,
            notice.category,
            notice.title,
            notice.detail ?? null,
            notice.linkSubdomain,
            notice.linkPath,
            notice.collapseKey ?? null,
            notice.expiresAt,
          ],
        );
        const id = rows[0].id;
        const usernames = [...notice.recipients.keys()];
        await client.query(
          `INSERT INTO notice_deliveries (notice_id, username, via_role)
           SELECT $1, username, via_role FROM unnest($2::text[], $3::text[]) AS t (username, via_role)`,
          [id, usernames, usernames.map((name) => notice.recipients.get(name) ?? null)],
        );
        await notify(client, [...previous.usernames, ...usernames]);
        const result: SendResult = { id, deliveries: usernames.length, replaced: previous.removed };
        if (idempotencyKey) {
          await client.query(
            'UPDATE notice_idempotency_keys SET result = $3 WHERE sender = $1 AND key = $2',
            [notice.sender, idempotencyKey, result],
          );
        }
        return { result, replayed: false };
      });
    },

    // Withdraws the sender's notice with this collapse key from everyone it reached.
    async withdraw(
      sender: string,
      collapseKey: string,
    ): Promise<{ withdrawn: boolean; deliveries: number }> {
      return transaction(pool, async (client) => {
        await lockKey(client, sender, collapseKey);
        const { removed, usernames } = await removeCollapsed(client, sender, collapseKey);
        await notify(client, usernames);
        return { withdrawn: removed, deliveries: usernames.length };
      });
    },

    async list(username: string, hiddenSenders: string[]): Promise<ListedNotice[]> {
      const { rows } = await pool.query(
        `SELECT n.id, n.sender_name, n.category, n.title, n.detail, n.link_subdomain,
                n.link_path, n.created_at, d.seen_at, d.read_at
         ${VISIBLE}
         ORDER BY n.created_at DESC, n.id
         LIMIT ${LIST_LIMIT}`,
        [username, hiddenSenders],
      );
      return rows.map((row) => ({
        id: row.id,
        sender: row.sender_name,
        category: row.category,
        title: row.title,
        detail: row.detail,
        link: { subdomain: row.link_subdomain, path: row.link_path },
        createdAt: (row.created_at as Date).toISOString(),
        seen: row.seen_at !== null,
        read: row.read_at !== null,
      }));
    },

    async summary(username: string, hiddenSenders: string[]): Promise<Summary> {
      const { rows } = await pool.query<Summary>(
        `SELECT count(*) FILTER (WHERE d.seen_at IS NULL)::int AS unseen,
                count(*) FILTER (WHERE d.read_at IS NULL)::int AS unread,
                left(encode(sha256(convert_to(coalesce(string_agg(
                  n.id::text || (d.read_at IS NULL)::text,
                  ',' ORDER BY n.id), ''), 'UTF8')), 'hex'), 16) AS marker
         ${VISIBLE}`,
        [username, hiddenSenders],
      );
      return rows[0];
    },

    // Changes the user's own deliveries only.
    async mark(username: string, action: MarkAction, ids: string[] | 'all'): Promise<number> {
      const set = {
        seen: 'seen_at = now()',
        read: 'read_at = now(), seen_at = coalesce(seen_at, now())',
        archive: 'archived_at = now()',
      }[action];
      const pending = { seen: 'seen_at', read: 'read_at', archive: 'archived_at' }[action];
      const which = ids === 'all' ? 'archived_at IS NULL' : 'notice_id = ANY($2::uuid[])';
      const result = await pool.query(
        `UPDATE notice_deliveries SET ${set}
         WHERE username = $1 AND ${pending} IS NULL AND ${which}`,
        ids === 'all' ? [username] : [username, ids],
      );
      const changed = result.rowCount ?? 0;
      if (changed > 0) await notify(pool, [username]);
      return changed;
    },
  };
}

export type NoticeStore = ReturnType<typeof noticeStore>;
