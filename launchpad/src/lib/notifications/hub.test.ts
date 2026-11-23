import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import type { Pool } from 'pg';
import { NoticeHub } from './hub';
import { noticeStore } from './store';
import { needsDatabase, testDatabase } from './testing';

function waitFor(check: () => boolean, ms = 5_000): Promise<void> {
  const deadline = Date.now() + ms;
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (check()) return resolve();
      if (Date.now() > deadline) return reject(new Error('timed out'));
      setTimeout(tick, 20);
    };
    tick();
  });
}

describe('notice hub', { skip: needsDatabase }, () => {
  let pool: Pool;
  let url: string;
  let schema: string;
  let drop: () => Promise<void>;

  const send = (username: string) =>
    noticeStore(pool).send({
      sender: 'job-runner',
      senderName: 'Jobs',
      category: 'job-finished',
      title: 'Job 7 finished',
      linkSubdomain: 'jobs',
      linkPath: '/runs/7',
      expiresAt: new Date(Date.now() + 60_000),
      recipients: new Map([[username, null]]),
    });

  before(async () => {
    ({ pool, url, schema, drop } = await testDatabase());
  });

  after(() => drop());

  it("calls a user's listeners when a send on any connection reaches them", async () => {
    const hub = new NoticeHub(url);
    let reader = 0;
    let other = 0;
    hub.subscribe('hub-reader', () => reader++);
    hub.subscribe('hub-other', () => other++);
    try {
      // The first connect calls every listener, for changes made before it listened.
      await waitFor(() => reader === 1);
      await send('hub-reader');
      await waitFor(() => reader === 2);
      assert.equal(other, 1);
    } finally {
      await hub.close();
    }
  });

  it('listens again after losing its connection, and catches up its listeners', async () => {
    const hub = new NoticeHub(url);
    let calls = 0;
    const unsubscribe = hub.subscribe('hub-reconnect', () => calls++);
    try {
      await waitFor(() => calls === 1);
      const { rowCount } = await pool.query(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name = $1 AND query = 'LISTEN launchpad_notices'",
        [schema],
      );
      assert.equal(rowCount, 1);
      await waitFor(() => calls === 2);
      await send('hub-reconnect');
      await waitFor(() => calls === 3);
      unsubscribe();
      await send('hub-reconnect');
      await new Promise((resolve) => setTimeout(resolve, 200));
      assert.equal(calls, 3);
    } finally {
      await hub.close();
    }
  });
});
