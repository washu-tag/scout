import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { Summary } from './store';
import { noticeStream } from './stream';

const summary = (unseen: number, unread = unseen): Summary => ({
  unseen,
  unread,
  marker: `m${unread}`,
});

async function readUntil(reader: ReadableStreamDefaultReader<Uint8Array>, text: RegExp) {
  const decoder = new TextDecoder();
  let seen = '';
  while (!text.test(seen)) {
    const { value, done } = await reader.read();
    if (done) return { seen, done: true };
    seen += decoder.decode(value);
  }
  return { seen, done: false };
}

function harness({ live = async () => true }: { live?: () => Promise<boolean> } = {}) {
  const controller = new AbortController();
  let current = summary(1);
  let onChange: () => void = () => {};
  let subscribed = 0;
  const stream = noticeStream({
    first: current,
    summary: async () => current,
    subscribe: (listener) => {
      subscribed += 1;
      onChange = listener;
      return () => {
        subscribed -= 1;
      };
    },
    sessionLive: live,
    signal: controller.signal,
    keepAliveMs: 20,
  });
  return {
    reader: stream.getReader(),
    abort: () => controller.abort(),
    change: (next: Summary) => {
      current = next;
      onChange();
    },
    subscribed: () => subscribed,
  };
}

describe('noticeStream', () => {
  it('starts with the counts, then sends them again only when they change', async () => {
    const h = harness();
    const first = await readUntil(h.reader, /event: counts/);
    assert.equal(first.seen, `event: counts\ndata: ${JSON.stringify(summary(1))}\n\n`);
    h.change(summary(1));
    h.change(summary(2));
    const next = await readUntil(h.reader, /event: counts/);
    assert.doesNotMatch(next.seen, /"unseen":1/);
    assert.match(next.seen, /data: \{"unseen":2,"unread":2,"marker":"m2"\}/);
    h.abort();
  });

  it('keeps the connection alive with comment lines', async () => {
    const h = harness();
    await readUntil(h.reader, /event: counts/);
    assert.equal((await readUntil(h.reader, /: keep-alive\n\n/)).done, false);
    h.abort();
  });

  it('sends a session event and closes once the session has ended', async () => {
    const h = harness({ live: async () => false });
    const result = await readUntil(h.reader, /event: session\ndata: \{\}\n\n/);
    assert.equal(result.done, false);
    assert.equal((await h.reader.read()).done, true);
    assert.equal(h.subscribed(), 0);
  });

  it('stays open when the session check itself fails', async () => {
    const h = harness({ live: () => Promise.reject(new Error('unexpected answer')) });
    await readUntil(h.reader, /event: counts/);
    const twice = await readUntil(h.reader, /(: keep-alive\n\n[^]*){2}/);
    assert.equal(twice.done, false);
    assert.doesNotMatch(twice.seen, /event: session/);
    h.abort();
  });

  it('stops listening when the reader goes away', async () => {
    const h = harness();
    await readUntil(h.reader, /event: counts/);
    assert.equal(h.subscribed(), 1);
    h.abort();
    assert.equal(h.subscribed(), 0);
    assert.equal((await h.reader.read()).done, true);
  });
});
