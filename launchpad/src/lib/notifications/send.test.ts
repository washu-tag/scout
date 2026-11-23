import assert from 'node:assert/strict';
import { after, before, beforeEach, describe, it } from 'node:test';
import type { Pool } from 'pg';
import type { Sender } from './registration';
import { staticRoleDirectory, type RoleDirectory } from './roles';
import { sendNotice, withdrawNotice, type SendDeps } from './send';
import { noticeStore } from './store';
import {
  ISSUER,
  LAUNCHPAD,
  needsDatabase,
  SENDER,
  testDatabase,
  testRealm,
  type Realm,
} from './testing';

const JOBS: Sender = {
  clientId: 'job-runner',
  displayName: 'Jobs',
  link: { subdomain: 'jobs', pathPrefixes: ['/'] },
  categories: ['job-finished'],
  source: 'test/jobs.yaml',
};

const ROLES = staticRoleDirectory({
  'request-tracker/reviewer': ['dana', 'erin'],
  'request-tracker/approver': ['erin', 'finn'],
  'job-runner/reviewer': ['gail'],
});

const body = (overrides: Record<string, unknown> = {}) =>
  JSON.stringify({
    recipients: { users: ['dana'] },
    category: 'review-needed',
    title: 'Request 42 is waiting for review',
    detail: 'Opened by a project member',
    link: '/requests/42',
    ...overrides,
  });

describe('send API', { skip: needsDatabase }, () => {
  let pool: Pool;
  let drop: () => Promise<void>;
  let realm: Realm;
  let bearer: string;

  const deps = (overrides: Partial<SendDeps> = {}): SendDeps => ({
    issuer: ISSUER,
    launchpadClientId: LAUNCHPAD,
    keys: realm.keys,
    senders: async () => new Map([SENDER, JOBS].map((sender) => [sender.clientId, sender])),
    store: async () => noticeStore(pool),
    roles: ROLES,
    disabled: [],
    rateLimit: 100,
    maxDeliveries: 10,
    ...overrides,
  });
  const send = (text: string, overrides: Partial<SendDeps> = {}, auth = bearer) =>
    sendNotice(auth, async () => text, deps(overrides));
  const listFor = (username: string) => noticeStore(pool).list(username, []);

  before(async () => {
    ({ pool, drop } = await testDatabase());
    realm = await testRealm();
    bearer = `Bearer ${await realm.token()}`;
  });

  after(() => drop());

  beforeEach(async () => {
    await pool.query(
      'TRUNCATE notices, notice_deliveries, notice_idempotency_keys, notice_send_windows',
    );
  });

  it('delivers a notice from the registered sender the token names', async () => {
    const reply = await send(body());
    assert.equal(reply.status, 201);
    assert.deepEqual((reply.body as { deliveries: number }).deliveries, 1);
    const [notice] = await listFor('dana');
    assert.equal(notice.sender, 'Requests');
    assert.deepEqual(notice.link, { subdomain: 'requests', path: '/requests/42' });
  });

  it('refuses a missing or invalid token, and a token without the send role', async () => {
    const invalid = await send(
      body(),
      {},
      `Bearer ${await realm.token({}, { key: realm.otherKey })}`,
    );
    assert.equal(invalid.status, 401);
    assert.equal(invalid.headers?.['WWW-Authenticate'], 'Bearer error="invalid_token"');
    assert.equal((await send(body(), {}, '')).status, 401);
    const noRole = await send(body(), {}, `Bearer ${await realm.token({ resource_access: {} })}`);
    assert.deepEqual(
      [noRole.status, (noRole.body as { error: string }).error],
      [403, 'missing_role'],
    );
  });

  it('refuses a client with no registration, and a sender the site has turned off', async () => {
    const stranger = `Bearer ${await realm.token({ azp: 'unknown-app' })}`;
    const unregistered = await send(body(), {}, stranger);
    assert.deepEqual(
      [unregistered.status, (unregistered.body as { error: string }).error],
      [403, 'unregistered_sender'],
    );
    const disabled = await send(body(), { disabled: ['request-tracker'] });
    assert.deepEqual(
      [disabled.status, (disabled.body as { error: string }).error],
      [403, 'sender_disabled'],
    );
    assert.deepEqual(await listFor('dana'), []);
  });

  it("can't speak for another sender: the token decides, not the body", async () => {
    const jobs = `Bearer ${await realm.token({ azp: 'job-runner' })}`;
    const reply = await send(body(), {}, jobs);
    assert.equal((reply.body as { error: string }).error, 'unknown_category');
    const ok = await send(body({ category: 'job-finished', link: '/runs/7' }), {}, jobs);
    assert.equal(ok.status, 201);
    assert.equal((await listFor('dana'))[0].sender, 'Jobs');
  });

  it('checks the body: JSON, the schema, the category and the link', async () => {
    const cases: [string, number, string][] = [
      ['not json', 400, 'invalid_json'],
      [body({ title: 'x'.repeat(121) }), 400, 'invalid_request'],
      [body({ title: 'two\nlines' }), 400, 'invalid_request'],
      [body({ recipients: {} }), 400, 'invalid_request'],
      [body({ recipients: { roles: ['job-runner:reviewer'] } }), 400, 'invalid_request'],
      [body({ category: 'other' }), 422, 'unknown_category'],
      [body({ link: 'https://requests.scout.example.edu/requests/42' }), 422, 'link_not_allowed'],
      [body({ link: '/settings' }), 422, 'link_not_allowed'],
      [body({ link: '/requests/../settings' }), 422, 'link_not_allowed'],
      [JSON.stringify({ padding: 'x'.repeat(200_000) }), 413, 'too_large'],
    ];
    for (const [text, status, error] of cases) {
      const reply = await send(text);
      assert.deepEqual(
        [reply.status, (reply.body as { error: string }).error],
        [status, error],
        text.slice(0, 80),
      );
    }
    assert.deepEqual(await listFor('dana'), []);
  });

  it("expands the sender's own roles, once per person, remembering the role", async () => {
    const reply = await send(
      body({ recipients: { users: ['ERIN'], roles: ['reviewer', 'approver'] } }),
    );
    assert.equal((reply.body as { deliveries: number }).deliveries, 3);
    const { rows } = await pool.query(
      'SELECT username, via_role FROM notice_deliveries ORDER BY username',
    );
    assert.deepEqual(rows, [
      { username: 'dana', via_role: 'reviewer' },
      { username: 'erin', via_role: null },
      { username: 'finn', via_role: 'approver' },
    ]);
    assert.deepEqual(await listFor('gail'), []);
  });

  it('refuses a role the sender lacks, and answers 503 when roles cannot be looked up', async () => {
    const unknown = await send(body({ recipients: { roles: ['admin'] } }));
    assert.deepEqual(
      [unknown.status, (unknown.body as { error: string }).error],
      [422, 'unknown_role'],
    );
    const broken: RoleDirectory = {
      members: async () => {
        throw new Error('scout-users is not deployed');
      },
    };
    const down = await send(body({ recipients: { roles: ['reviewer'] } }), { roles: broken });
    assert.deepEqual(
      [down.status, (down.body as { error: string }).error],
      [503, 'role_lookup_unavailable'],
    );
  });

  it('caps the deliveries a notice may have once its roles are expanded', async () => {
    for (const recipients of [
      { users: ['a', 'b', 'c'] },
      { roles: ['reviewer', 'approver'] },
      { users: ['a', 'b'], roles: ['reviewer'] },
    ]) {
      const reply = await send(body({ recipients }), { maxDeliveries: 2 });
      assert.deepEqual(
        [reply.status, (reply.body as { error: string }).error],
        [422, 'too_many_recipients'],
        JSON.stringify(recipients),
      );
    }
    assert.equal(
      (await send(body({ recipients: { roles: ['reviewer'] } }), { maxDeliveries: 2 })).status,
      201,
    );
  });

  it('limits calls per sender per minute', async () => {
    const replies = [];
    for (let i = 0; i < 4; i++) replies.push(await send(body(), { rateLimit: 3 }));
    assert.deepEqual(
      replies.map((r) => r.status),
      [201, 201, 201, 429],
    );
    assert.match(replies[3].headers?.['Retry-After'] ?? '', /^\d+$/);
    const jobs = `Bearer ${await realm.token({ azp: 'job-runner' })}`;
    const other = await send(body({ category: 'job-finished', link: '/' }), { rateLimit: 3 }, jobs);
    assert.equal(other.status, 201);
  });

  it('returns the first result for a repeated idempotency key', async () => {
    const first = await send(body({ idempotencyKey: 'request-42-opened' }));
    const again = await send(body({ idempotencyKey: 'request-42-opened', title: 'Changed' }));
    assert.deepEqual([again.status, again.body], [201, first.body]);
    assert.equal(again.headers?.['Idempotent-Replayed'], 'true');
    assert.equal((await listFor('dana')).length, 1);
  });

  it('replaces by collapse key and withdraws for everyone it reached', async () => {
    await send(body({ collapseKey: 'request-42', recipients: { roles: ['reviewer'] } }));
    const replaced = await send(
      body({
        collapseKey: 'request-42',
        title: 'Request 42 needs another look',
        recipients: { roles: ['approver'] },
      }),
    );
    assert.equal((replaced.body as { replaced: boolean }).replaced, true);
    assert.deepEqual(await listFor('dana'), []);
    assert.deepEqual(
      (await listFor('finn')).map((n) => n.title),
      ['Request 42 needs another look'],
    );

    const withdraw = (key: string | null, auth = bearer) => withdrawNotice(auth, key, deps());
    assert.deepEqual((await withdraw('request-42')).body, { withdrawn: true, deliveries: 2 });
    assert.deepEqual(await listFor('erin'), []);
    assert.deepEqual((await withdraw('request-42')).body, { withdrawn: false, deliveries: 0 });
    assert.equal((await withdraw(null)).status, 400);
    assert.equal((await withdraw('request-42', '')).status, 401);
  });

  it("withdraws only the sender's own notice", async () => {
    await send(body({ collapseKey: 'shared-key' }));
    const jobs = `Bearer ${await realm.token({ azp: 'job-runner' })}`;
    const reply = await withdrawNotice(jobs, 'shared-key', deps());
    assert.deepEqual(reply.body, { withdrawn: false, deliveries: 0 });
    assert.equal((await listFor('dana')).length, 1);
  });

  it('answers 503 when the database is unreachable', async () => {
    const reply = await send(body(), {
      store: async () => {
        throw new Error('connection refused');
      },
    });
    assert.equal(reply.status, 503);
  });
});
