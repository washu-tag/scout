import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  indexSenders,
  isNormalPath,
  parseSenderText,
  SENDER_API_VERSION,
  SENDER_KIND,
  validateSender,
} from './registration';

const valid = {
  apiVersion: SENDER_API_VERSION,
  kind: SENDER_KIND,
  displayName: 'Requests',
  clientId: 'request-tracker',
  link: { subdomain: 'requests', pathPrefixes: ['/requests', '/review/'] },
  categories: ['review-needed', 'job-finished'],
};

describe('validateSender', () => {
  it('accepts a complete registration', () => {
    const { sender, diagnostics } = validateSender(valid, 'ns/cm/senders.yaml');
    assert.deepEqual(diagnostics, []);
    assert.deepEqual(sender, {
      clientId: 'request-tracker',
      displayName: 'Requests',
      link: { subdomain: 'requests', pathPrefixes: ['/requests', '/review/'] },
      categories: ['review-needed', 'job-finished'],
      source: 'ns/cm/senders.yaml',
    });
  });

  it('skips a document of another type or version', () => {
    for (const doc of [
      { ...valid, apiVersion: 'launchpad.scout.xnat.org/v9' },
      { ...valid, kind: 'Catalog' },
    ]) {
      const { sender, diagnostics } = validateSender(doc, 's');
      assert.equal(sender, undefined);
      assert.match(diagnostics[0].message, /unknown document type/);
    }
  });

  it('skips the whole registration for any invalid field', () => {
    const cases: Record<string, unknown>[] = [
      { displayName: '' },
      { displayName: 'x'.repeat(61) },
      { clientId: '../admin' },
      { link: { subdomain: 'Requests.Example', pathPrefixes: ['/'] } },
      { link: { subdomain: 'requests', pathPrefixes: [] } },
      { link: { subdomain: 'requests' } },
      { link: { subdomain: 'requests', pathPrefixes: ['//evil.example'] } },
      { link: { subdomain: 'requests', pathPrefixes: ['/a/../admin'] } },
      { link: { subdomain: 'requests', pathPrefixes: ['/a?b'] } },
      { categories: [] },
      { categories: ['Review Needed'] },
    ];
    for (const change of cases) {
      const { sender, diagnostics } = validateSender({ ...valid, ...change }, 's');
      assert.equal(sender, undefined, JSON.stringify(change));
      assert.match(diagnostics[0].message, /^sender skipped/);
    }
  });

  it('ignores unknown fields with a diagnostic', () => {
    const { sender, diagnostics } = validateSender(
      { ...valid, icon: 'bell', link: { ...valid.link, host: 'evil.example' } },
      's',
    );
    assert.equal(sender?.link.subdomain, 'requests');
    assert.deepEqual(
      diagnostics.map((d) => d.message),
      ['unknown field "icon" ignored', 'unknown field "link.host" ignored'],
    );
  });
});

describe('isNormalPath', () => {
  it('accepts rooted paths in normal form', () => {
    for (const path of ['/', '/requests', '/review/', '/a%20b'])
      assert.equal(isNormalPath(path), true);
  });

  it('rejects paths that could resolve anywhere else', () => {
    for (const path of [
      'requests',
      '//host',
      '/\\host',
      '/a/./b',
      '/a/%2e%2e/b',
      '/a%2Fb',
      '/a b',
      '/a#x',
    ]) {
      assert.equal(isNormalPath(path), false, path);
    }
  });
});

describe('parseSenderText', () => {
  it('reads several documents from one key and reports YAML errors', () => {
    const text = [
      `apiVersion: ${SENDER_API_VERSION}`,
      `kind: ${SENDER_KIND}`,
      'displayName: Jobs',
      'clientId: jobs',
      'link: { subdomain: jobs, pathPrefixes: [/] }',
      'categories: [job-finished]',
      '---',
      'displayName: [broken',
    ].join('\n');
    const { senders, diagnostics } = parseSenderText(text, 'ns/cm/senders.yaml');
    assert.deepEqual(
      senders.map((s) => [s.clientId, s.source]),
      [['jobs', 'ns/cm/senders.yaml (document 1)']],
    );
    assert.match(diagnostics[0].message, /YAML parse error/);
  });
});

describe('indexSenders', () => {
  it('refuses every registration of a client registered twice', () => {
    const one = validateSender(valid, 'a').sender!;
    const two = validateSender(
      { ...valid, link: { subdomain: 'other', pathPrefixes: ['/'] } },
      'b',
    ).sender!;
    const jobs = validateSender({ ...valid, clientId: 'jobs' }, 'c').sender!;
    const { byClient, diagnostics } = indexSenders([one, two, jobs]);
    assert.deepEqual([...byClient.keys()], ['jobs']);
    assert.equal(diagnostics.length, 2);
    assert.match(diagnostics[0].message, /registered more than once \(a, b\)/);
  });
});
