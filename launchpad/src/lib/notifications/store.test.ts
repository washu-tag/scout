import assert from 'node:assert/strict';
import { after, before, beforeEach, describe, it } from 'node:test';
import { Client, type Pool } from 'pg';
import { cleanup, migrate } from './db';
import { CHANNEL, noticeStore, notifyPayloads, type NewNotice, type NoticeStore } from './store';
import { needsDatabase, testDatabase } from './testing';

describe('notifyPayloads', () => {
  it('names each user once, in payloads under the 8000-byte limit', () => {
    const names = Array.from({ length: 2000 }, (_, i) => `user-${i}@example.edu`);
    const payloads = notifyPayloads([...names, ...names]);
    assert.ok(payloads.length > 1);
    for (const payload of payloads) assert.ok(Buffer.byteLength(payload) < 8000);
    assert.deepEqual(
      payloads.flatMap((payload) => JSON.parse(payload).users),
      names,
    );
  });
});

describe('notice store', { skip: needsDatabase }, () => {
  let pool: Pool;
  let url: string;
  let drop: () => Promise<void>;
  let store: NoticeStore;

  const notice = (overrides: Partial<NewNotice> = {}): NewNotice => ({
    sender: 'request-tracker',
    senderName: 'Requests',
    category: 'review-needed',
    title: 'Request 42 is waiting for review',
    detail: 'Submitted by a project member',
    linkSubdomain: 'requests',
    linkPath: '/requests/42',
    expiresAt: new Date(Date.now() + 86_400_000),
    recipients: new Map([
      ['alice', null],
      ['bob', 'reviewer'],
    ]),
    ...overrides,
  });

  before(async () => {
    ({ pool, url, drop } = await testDatabase());
    store = noticeStore(pool);
  });

  after(() => drop());

  beforeEach(async () => {
    await pool.query(
      'TRUNCATE notices, notice_deliveries, notice_idempotency_keys, notice_send_windows',
    );
  });

  it('migrates once, however often it starts', async () => {
    await migrate(pool);
    await Promise.all([migrate(pool), migrate(pool)]);
    const { rows } = await pool.query('SELECT version FROM launchpad_schema_migrations');
    assert.deepEqual(rows, [{ version: 1 }]);
  });

  it("lists a delivery to each recipient, with each one's own counts", async () => {
    const { result } = await store.send(notice());
    assert.deepEqual(
      { ...result, id: typeof result.id },
      { id: 'string', deliveries: 2, replaced: false },
    );
    const [listed] = await store.list('alice', []);
    assert.deepEqual(
      { ...listed, id: listed.id === result.id, createdAt: typeof listed.createdAt },
      {
        id: true,
        sender: 'Requests',
        category: 'review-needed',
        title: 'Request 42 is waiting for review',
        detail: 'Submitted by a project member',
        link: { subdomain: 'requests', path: '/requests/42' },
        createdAt: 'string',
        seen: false,
        read: false,
      },
    );
    assert.deepEqual(await store.list('carol', []), []);
    const { rows } = await pool.query(
      'SELECT username, via_role FROM notice_deliveries ORDER BY username',
    );
    assert.deepEqual(rows, [
      { username: 'alice', via_role: null },
      { username: 'bob', via_role: 'reviewer' },
    ]);
  });

  it('keeps seen, read and archived per person', async () => {
    const { result } = await store.send(notice());
    const before = await store.summary('alice', []);
    assert.deepEqual(
      { ...before, marker: typeof before.marker },
      { unseen: 1, unread: 1, marker: 'string' },
    );

    assert.equal(await store.mark('alice', 'seen', [result.id]), 1);
    const seen = await store.summary('alice', []);
    assert.deepEqual([seen.unseen, seen.unread, seen.marker], [0, 1, before.marker]);

    assert.equal(await store.mark('alice', 'read', [result.id]), 1);
    const read = await store.summary('alice', []);
    assert.deepEqual([read.unseen, read.unread], [0, 0]);
    assert.notEqual(read.marker, before.marker);

    assert.equal(await store.mark('alice', 'archive', [result.id]), 1);
    assert.deepEqual(await store.list('alice', []), []);
    assert.deepEqual((await store.summary('bob', [])).unread, 1);
  });

  it("changes only the reader's own deliveries", async () => {
    const { result } = await store.send(notice({ recipients: new Map([['bob', null]]) }));
    assert.equal(await store.mark('alice', 'read', [result.id]), 0);
    assert.equal(await store.mark('alice', 'read', 'all'), 0);
    assert.equal(await store.mark('alice', 'archive', [result.id]), 0);
    assert.equal((await store.summary('bob', [])).unread, 1);
  });

  it('marks everything read at once', async () => {
    await store.send(notice());
    await store.send(notice({ title: 'Job 7 finished', category: 'job-finished' }));
    assert.equal(await store.mark('alice', 'read', 'all'), 2);
    assert.deepEqual(
      (await store.list('alice', [])).map((n) => n.read),
      [true, true],
    );
    assert.equal((await store.summary('bob', [])).unread, 2);
  });

  it('counts only the notices the list returns', async () => {
    for (let i = 0; i < 105; i++) {
      await store.send(notice({ recipients: new Map([['alice', null]]) }));
    }
    const listed = await store.list('alice', []);
    assert.equal(listed.length, 100);
    assert.equal((await store.summary('alice', [])).unseen, 100);
    await store.mark(
      'alice',
      'seen',
      listed.map((n) => n.id),
    );
    assert.equal((await store.summary('alice', [])).unseen, 0);
  });

  it("replaces the sender's earlier notice with the same collapse key", async () => {
    await store.send(notice({ collapseKey: 'request-42' }));
    const second = await store.send(
      notice({
        collapseKey: 'request-42',
        title: 'Request 42 changed',
        recipients: new Map([['carol', null]]),
      }),
    );
    assert.equal(second.result.replaced, true);
    assert.deepEqual(await store.list('alice', []), []);
    assert.deepEqual(
      (await store.list('carol', [])).map((n) => n.title),
      ['Request 42 changed'],
    );
    const other = await store.send(notice({ sender: 'job-runner', collapseKey: 'request-42' }));
    assert.equal(other.result.replaced, false);
    assert.equal((await store.list('carol', [])).length, 1);
  });

  it('serializes sends that share a collapse key', async () => {
    await Promise.all(
      Array.from({ length: 5 }, (_, i) =>
        store.send(notice({ collapseKey: 'request-42', title: `Version ${i}` })),
      ),
    );
    const { rows } = await pool.query('SELECT count(*)::int AS n FROM notices');
    assert.deepEqual(rows, [{ n: 1 }]);
    assert.equal((await store.list('alice', [])).length, 1);
  });

  it('withdraws a notice from everyone it reached', async () => {
    await store.send(notice({ collapseKey: 'request-42' }));
    await store.send(notice({ collapseKey: 'request-43' }));
    assert.deepEqual(await store.withdraw('request-tracker', 'request-42'), {
      withdrawn: true,
      deliveries: 2,
    });
    for (const reader of ['alice', 'bob']) assert.equal((await store.list(reader, [])).length, 1);
    assert.deepEqual(await store.withdraw('request-tracker', 'request-42'), {
      withdrawn: false,
      deliveries: 0,
    });
    assert.deepEqual(await store.withdraw('job-runner', 'request-43'), {
      withdrawn: false,
      deliveries: 0,
    });
  });

  it('sends once per idempotency key within 24 hours', async () => {
    const first = await store.send(notice(), 'send-1');
    const again = await store.send(notice({ title: 'Different' }), 'send-1');
    assert.deepEqual(again, { result: first.result, replayed: true });
    assert.deepEqual(await store.replay('request-tracker', 'send-1'), first.result);
    assert.equal(await store.replay('job-runner', 'send-1'), null);
    assert.equal((await store.list('alice', [])).length, 1);

    await pool.query("UPDATE notice_idempotency_keys SET created_at = now() - interval '25 hours'");
    assert.equal(await store.replay('request-tracker', 'send-1'), null);
    const later = await store.send(notice(), 'send-1');
    assert.equal(later.replayed, false);
    assert.notEqual(later.result.id, first.result.id);
  });

  it('sends once when two requests with one idempotency key race', async () => {
    const results = await Promise.all([store.send(notice(), 'race'), store.send(notice(), 'race')]);
    assert.deepEqual(results.map((r) => r.replayed).sort(), [false, true]);
    assert.equal(results[0].result.id, results[1].result.id);
    assert.equal((await store.list('alice', [])).length, 1);
  });

  it('hides expired notices and disabled senders, and deletes past retention', async () => {
    await store.send(notice({ title: 'Expired', expiresAt: new Date(Date.now() + 1000) }));
    await store.send(notice({ title: 'From a disabled sender', sender: 'job-runner' }));
    await store.send(notice({ title: 'Current' }));
    await pool.query(
      "UPDATE notices SET expires_at = now() - interval '1 second' WHERE title = 'Expired'",
    );
    assert.deepEqual(
      (await store.list('alice', ['job-runner'])).map((n) => n.title),
      ['Current'],
    );
    assert.equal((await store.summary('alice', ['job-runner'])).unread, 1);
    await cleanup(pool);
    const { rows } = await pool.query('SELECT title FROM notices ORDER BY title');
    assert.deepEqual(
      rows.map((r) => r.title),
      ['Current', 'From a disabled sender'],
    );
    const deliveries = await pool.query('SELECT count(*)::int AS n FROM notice_deliveries');
    assert.equal(deliveries.rows[0].n, 4);
  });

  it("counts each sender's calls per minute", async () => {
    assert.equal((await store.countCall('request-tracker')).count, 1);
    assert.equal((await store.countCall('request-tracker')).count, 2);
    const other = await store.countCall('job-runner');
    assert.equal(other.count, 1);
    assert.ok(other.retryAfter >= 1 && other.retryAfter <= 60);
  });

  it('tells listeners whose notices changed, never what they say', async () => {
    const listener = new Client({ connectionString: url });
    await listener.connect();
    const payloads: string[] = [];
    listener.on('notification', (message) => payloads.push(message.payload ?? ''));
    await listener.query(`LISTEN ${CHANNEL}`);
    try {
      await store.send(notice({ collapseKey: 'k' }));
      await store.send(notice({ collapseKey: 'k', recipients: new Map([['carol', null]]) }));
      await store.withdraw('request-tracker', 'k');
      await new Promise((resolve) => setTimeout(resolve, 200));
      const mine = payloads
        .map((p) => JSON.parse(p))
        .filter((p) => p.users.some((u: string) => ['alice', 'bob', 'carol'].includes(u)));
      assert.deepEqual(mine, [
        { users: ['alice', 'bob'] },
        { users: ['alice', 'bob', 'carol'] },
        { users: ['carol'] },
      ]);
      assert.ok(payloads.every((p) => !p.includes('Request 42')));
    } finally {
      await listener.end();
    }
  });
});
