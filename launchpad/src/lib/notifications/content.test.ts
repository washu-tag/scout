import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { checkRequest, sendSchema, senderLink, underPrefix } from './content';
import { SENDER } from './testing';

const NOW = new Date('2026-11-23T12:00:00Z');

function request(overrides: Record<string, unknown> = {}) {
  return sendSchema.parse({
    recipients: { users: ['alice'] },
    category: 'review-needed',
    title: 'A request is waiting for review',
    link: '/requests/42',
    ...overrides,
  });
}

describe('sendSchema', () => {
  it('lowercases usernames and defaults the missing recipient list', () => {
    const parsed = request({ recipients: { users: [' Alice '] } });
    assert.deepEqual(parsed.recipients, { users: ['alice'], roles: [] });
  });

  it('needs at least one recipient', () => {
    assert.equal(sendSchema.safeParse({ ...request(), recipients: {} }).success, false);
  });

  it('takes roles by name only, never another client', () => {
    for (const role of ['other-client:admin', 'other/admin', '../admin', '']) {
      const body = { ...request(), recipients: { roles: [role] } };
      assert.equal(sendSchema.safeParse(body).success, false, role);
    }
    assert.equal(
      sendSchema.safeParse({ ...request(), recipients: { roles: ['reviewer'] } }).success,
      true,
    );
  });

  it('caps the title and detail, counting characters rather than bytes', () => {
    assert.equal(sendSchema.safeParse({ ...request(), title: 'x'.repeat(120) }).success, true);
    assert.equal(sendSchema.safeParse({ ...request(), title: 'x'.repeat(121) }).success, false);
    assert.equal(sendSchema.safeParse({ ...request(), title: 'é'.repeat(120) }).success, true);
    assert.equal(sendSchema.safeParse({ ...request(), detail: 'x'.repeat(1000) }).success, true);
    assert.equal(sendSchema.safeParse({ ...request(), detail: 'x'.repeat(1001) }).success, false);
    assert.equal(sendSchema.safeParse({ ...request(), title: '   ' }).success, false);
  });

  it('takes plain text, with line breaks in the detail only', () => {
    for (const title of ['line\nbreak', 'tab\there', 'bell\u0007', 'flip \u202e txet']) {
      assert.equal(sendSchema.safeParse({ ...request(), title }).success, false, title);
    }
    const detail = 'docker pull ghcr.io/example/app:1.2\n  then run it';
    assert.equal(sendSchema.safeParse({ ...request(), detail }).data?.detail, detail);
    for (const bad of ['crlf\r\nbreak', 'tab\there', 'bell\u0007', 'flip \u202e txet']) {
      assert.equal(sendSchema.safeParse({ ...request(), detail: bad }).success, false, bad);
    }
    assert.equal(
      sendSchema.safeParse({ ...request(), title: '<b>shown as typed</b>' }).success,
      true,
    );
  });

  it('rejects unknown fields, so a misspelt key is not silently dropped', () => {
    assert.equal(sendSchema.safeParse({ ...request(), colapseKey: 'x' }).success, false);
    assert.equal(sendSchema.safeParse({ ...request(), url: 'https://example.com' }).success, false);
  });
});

describe('underPrefix', () => {
  it('covers the prefix itself and paths below it, not siblings', () => {
    assert.equal(underPrefix('/requests', '/requests'), true);
    assert.equal(underPrefix('/requests/7', '/requests'), true);
    assert.equal(underPrefix('/requests-old', '/requests'), false);
    assert.equal(underPrefix('/review', '/review/'), false);
    assert.equal(underPrefix('/review/7', '/review/'), true);
    assert.equal(underPrefix('/anything', '/'), true);
  });
});

describe('senderLink', () => {
  it('keeps a path under a registered prefix, with its query and fragment', () => {
    assert.equal(senderLink('/requests/42?tab=files#top', SENDER), '/requests/42?tab=files#top');
  });

  it('rejects anything that is not a path under a registered prefix', () => {
    const rejected = [
      'https://requests.scout.example.edu/requests/1',
      '//evil.example/requests',
      '/\\evil.example',
      'requests/1',
      '/admin',
      '/requests/../admin',
      '/requests/%2e%2e/admin',
      '/requests/..%2fadmin',
      '/requests/a b',
      '/requests/\u0000',
      'javascript:alert(1)',
    ];
    for (const link of rejected) assert.equal(senderLink(link, SENDER), null, link);
  });
});

describe('checkRequest', () => {
  it('accepts a registered category and link, expiring after the retention period', () => {
    const checked = checkRequest(request(), SENDER, NOW);
    assert.deepEqual(checked, {
      ok: true,
      link: '/requests/42',
      expiresAt: new Date('2027-02-21T12:00:00Z'),
    });
  });

  it('keeps an earlier expiry, and refuses one already past', () => {
    const soon = checkRequest(request({ expiresAt: '2026-11-24T00:00:00Z' }), SENDER, NOW);
    assert.equal(soon.ok && soon.expiresAt.toISOString(), '2026-11-24T00:00:00.000Z');
    const late = checkRequest(request({ expiresAt: '2030-01-01T00:00:00Z' }), SENDER, NOW);
    assert.equal(late.ok && late.expiresAt.toISOString(), '2027-02-21T12:00:00.000Z');
    const past = checkRequest(request({ expiresAt: '2026-11-01T00:00:00Z' }), SENDER, NOW);
    assert.equal(!past.ok && past.error, 'already_expired');
  });

  it('refuses a category or link the registration does not name', () => {
    const category = checkRequest(request({ category: 'other' }), SENDER, NOW);
    assert.equal(!category.ok && category.error, 'unknown_category');
    const link = checkRequest(request({ link: '/settings' }), SENDER, NOW);
    assert.equal(!link.ok && link.error, 'link_not_allowed');
  });
});
