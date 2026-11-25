// Run with: node --test ansible/roles/jupyter/files/code-server-scout/chat-models.test.js
const assert = require('node:assert/strict');
const { describe, it } = require('node:test');
const { PLACEHOLDER, chatModelKeys, mergeChatModels } = require('./chat-models');

function provider(name, models, apiKey = PLACEHOLDER) {
  return {
    name,
    vendor: 'customendpoint',
    apiKey,
    models: models.map(([id, url]) => ({ id, url, requestHeaders: { 'x-api-key': apiKey } })),
  };
}

describe('mergeChatModels', () => {
  it('writes the site models with the key when the user has none', () => {
    const site = [provider('Gateway', [['m1', 'https://gw/v1']])];
    const { providers, changed } = mergeChatModels(site, null, 'k1');
    assert.ok(changed);
    assert.deepEqual(providers, [provider('Gateway', [['m1', 'https://gw/v1']], 'k1')]);
  });

  it('applies a changed URL, an added model and a removed model', () => {
    const current = [
      provider(
        'Gateway',
        [
          ['m1', 'https://old/v1'],
          ['gone', 'https://old/v1'],
        ],
        'k1',
      ),
    ];
    const site = [
      provider('Gateway', [
        ['m1', 'https://new/v1'],
        ['m2', 'https://new/v1'],
      ]),
    ];
    const { providers, changed } = mergeChatModels(site, current, 'k1');
    assert.ok(changed);
    assert.deepEqual(providers, [
      provider(
        'Gateway',
        [
          ['m1', 'https://new/v1'],
          ['m2', 'https://new/v1'],
        ],
        'k1',
      ),
    ]);
  });

  it("keeps the user's own providers after the site's", () => {
    const mine = provider('Mine', [['x', 'https://mine/v1']], 'mykey');
    const site = [provider('Gateway', [['m1', 'https://gw/v1']])];
    const { providers } = mergeChatModels(site, [mine], 'k1');
    assert.deepEqual(
      providers.map((p) => p.name),
      ['Gateway', 'Mine'],
    );
    assert.deepEqual(providers[1], mine);
  });

  it('leaves the placeholder when there is no key yet', () => {
    const site = [provider('Gateway', [['m1', 'https://gw/v1']])];
    const { providers } = mergeChatModels(site, [], undefined);
    assert.deepEqual(providers, site);
  });

  it('reports no change when the file is already up to date', () => {
    const site = [provider('Gateway', [['m1', 'https://gw/v1']])];
    const current = [provider('Gateway', [['m1', 'https://gw/v1']], 'k1')];
    assert.equal(mergeChatModels(site, current, 'k1').changed, false);
  });

  it("keeps the user's per-model settings over the site's defaults", () => {
    const site = [
      {
        ...provider('Gateway', [
          ['m1', 'u'],
          ['m2', 'u'],
        ]),
        settings: { m1: { reasoningEffort: 'medium' }, m2: { reasoningEffort: 'medium' } },
      },
    ];
    const current = [
      {
        ...provider('Gateway', [['m1', 'u']], 'k1'),
        settings: { m1: { reasoningEffort: 'high', verbosity: 'low' } },
      },
    ];
    const { providers } = mergeChatModels(site, current, 'k1');
    assert.deepEqual(providers[0].settings, {
      m1: { reasoningEffort: 'high', verbosity: 'low' },
      m2: { reasoningEffort: 'medium' },
    });
    assert.equal(mergeChatModels(site, providers, 'k1').changed, false);
  });

  it('keeps settings the user added to a site provider without defaults', () => {
    const site = [provider('Gateway', [['m1', 'u']])];
    const current = [
      { ...provider('Gateway', [['m1', 'u']], 'k1'), settings: { m1: { reasoningEffort: 'low' } } },
    ];
    const { providers } = mergeChatModels(site, current, 'k1');
    assert.deepEqual(providers[0].settings, { m1: { reasoningEffort: 'low' } });
  });
});

describe('chatModelKeys', () => {
  it('finds keys in provider apiKey and x-api-key headers, skipping the placeholder', () => {
    const providers = [
      provider('A', [['m1', 'u']], 'k1'),
      { name: 'B', models: [{ id: 'm', requestHeaders: { 'X-Api-Key': 'k2' } }] },
      provider('C', [['m1', 'u']]),
    ];
    assert.deepEqual(chatModelKeys(providers), ['k1', 'k1', 'k2']);
  });
});
