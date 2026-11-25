import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { contentSecurityPolicy, frameAllow } from './csp';

function directives(policy: string): Map<string, string> {
  return new Map(
    policy.split('; ').map((part) => {
      const [name, ...values] = part.split(' ');
      return [name, values.join(' ')];
    }),
  );
}

describe('contentSecurityPolicy', () => {
  it('allows scripts only by nonce and frames only from Scout subdomains', () => {
    const policy = directives(
      contentSecurityPolicy({ nonce: 'abc123', host: 'scout.example.edu', dev: false }),
    );
    assert.strictEqual(policy.get('script-src'), "'self' 'nonce-abc123' 'strict-dynamic'");
    assert.strictEqual(policy.get('frame-src'), 'https://*.scout.example.edu');
    assert.strictEqual(policy.get('frame-ancestors'), "'none'");
    assert.strictEqual(policy.get('object-src'), "'none'");
    assert.strictEqual(policy.get('connect-src'), "'self'");
    assert.ok(policy.has('upgrade-insecure-requests'));
    assert.ok(!policy.get('script-src')!.includes('unsafe'));
  });

  it('drops the port from the host and loosens only what development needs', () => {
    const policy = directives(
      contentSecurityPolicy({ nonce: 'n', host: 'localhost:3000', dev: true }),
    );
    assert.strictEqual(policy.get('frame-src'), 'https://*.localhost');
    assert.ok(policy.get('script-src')!.includes("'unsafe-eval'"));
    assert.strictEqual(policy.get('connect-src'), "'self' ws:");
    assert.ok(!policy.has('upgrade-insecure-requests'));
  });

  it('frames nothing when the host is not a plain hostname', () => {
    const policy = directives(
      contentSecurityPolicy({ nonce: 'n', host: 'evil; script-src *', dev: false }),
    );
    assert.strictEqual(policy.get('frame-src'), "'none'");
  });
});

describe('frameAllow', () => {
  it('delegates the clipboard and fullscreen to Scout subdomains only', () => {
    assert.strictEqual(
      frameAllow('scout.example.edu:443'),
      'clipboard-read https://*.scout.example.edu; clipboard-write https://*.scout.example.edu; fullscreen https://*.scout.example.edu',
    );
  });

  it('falls back to the frame src origin when the host is not a plain hostname', () => {
    assert.strictEqual(frameAllow('evil *'), 'clipboard-read; clipboard-write; fullscreen');
  });
});
