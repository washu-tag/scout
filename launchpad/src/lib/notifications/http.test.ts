import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { readLimited } from './http';

const post = (body: BodyInit, headers: Record<string, string> = {}) =>
  new Request('http://launchpad/api/notifications', {
    method: 'POST',
    body,
    headers,
    duplex: 'half',
  } as RequestInit);

describe('readLimited', () => {
  it('returns a body within the limit', async () => {
    assert.equal(await readLimited(post('{"a":1}'), 16), '{"a":1}');
  });

  it('refuses a declared length over the limit without reading', async () => {
    assert.equal(await readLimited(post('x'.repeat(10), { 'content-length': '99' }), 16), null);
  });

  it('stops reading a streamed body once it passes the limit', async () => {
    let pulled = 0;
    const endless = new ReadableStream<Uint8Array>({
      pull(controller) {
        pulled += 1;
        controller.enqueue(new Uint8Array(8));
      },
    });
    assert.equal(await readLimited(post(endless), 16), null);
    assert.ok(pulled < 10);
  });
});
