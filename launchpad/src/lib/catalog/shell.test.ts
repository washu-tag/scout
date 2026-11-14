import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { assemble, type Origin } from './assemble';
import { CATALOG_API_VERSION, CATALOG_KIND, validateDocument } from './schema';
import {
  chipForUrl,
  chipTarget,
  deepPath,
  embeddedChip,
  frameUrl,
  isChipOrigin,
  openRequest,
  recentApps,
  serviceHost,
  shellLocation,
  shellSections,
} from './shell';
import type { Catalog } from './types';

const ORIGIN: Origin = { protocol: 'https', host: 'scout.example.edu' };

function catalogFrom(document: Record<string, unknown>, source = 'test/apps.yaml'): Catalog {
  return validateDocument(
    { apiVersion: CATALOG_API_VERSION, kind: CATALOG_KIND, ...document },
    source,
  );
}

const CATALOG = catalogFrom({
  groups: [
    { id: 'core', title: 'Core', weight: 10 },
    { id: 'admin', title: 'Admin', icon: 'cog', weight: 30, audience: 'admin' },
    { id: 'docs', title: 'Docs', weight: 20 },
  ],
  chips: [
    {
      id: 'notebooks',
      title: 'Notebooks',
      link: { subdomain: 'jupyter' },
      group: 'core',
      weight: 30,
    },
    { id: 'chat', title: 'Chat', link: { subdomain: 'chat' }, group: 'core', weight: 10 },
    { id: 'guide', title: 'Guide', link: { url: 'https://docs.example.edu' }, group: 'docs' },
    {
      id: 'users',
      title: 'Users',
      link: { path: '/admin/users' },
      group: 'admin',
      audience: 'admin',
    },
  ],
});

describe('shellSections', () => {
  it('keeps every chip in page order, one section per group', () => {
    const sections = shellSections(assemble(CATALOG, { origin: ORIGIN, isAdmin: true }));
    assert.deepStrictEqual(
      sections.map((section) => [section.id, section.chips.map((chip) => chip.id)]),
      [
        ['core', ['chat', 'notebooks']],
        ['docs', ['guide']],
        ['admin', ['users']],
      ],
    );
    assert.deepStrictEqual([sections[2].title, sections[2].icon], ['Admin', 'cog']);

    const userSections = shellSections(assemble(CATALOG, { origin: ORIGIN, isAdmin: false }));
    assert.deepStrictEqual(
      userSections.map((section) => section.id),
      ['core', 'docs'],
    );
  });

  it('gives a duplicated id to the first chip in page order', () => {
    const first = catalogFrom(
      { chips: [{ id: 'chat', title: 'Chat', link: { subdomain: 'chat' }, weight: 10 }] },
      'a/apps.yaml',
    );
    const second = catalogFrom(
      { chips: [{ id: 'chat', title: 'Other chat', link: { subdomain: 'other' }, weight: 20 }] },
      'b/apps.yaml',
    );
    const merged: Catalog = {
      chips: [...first.chips, ...second.chips],
      groups: [],
      diagnostics: [],
    };
    const sections = shellSections(assemble(merged, { origin: ORIGIN, isAdmin: false }));
    assert.deepStrictEqual(
      sections.flatMap((section) => section.chips.map((chip) => chip.title)),
      ['Chat'],
    );
  });
});

describe('chipTarget', () => {
  const chips = shellSections(assemble(CATALOG, { origin: ORIGIN, isAdmin: true })).flatMap(
    (section) => section.chips,
  );
  const byId = (id: string) => chips.find((chip) => chip.id === id)!;

  it('frames services, routes launchpad pages in place, and opens outside URLs per newTab', () => {
    assert.deepStrictEqual(chipTarget(byId('chat')), {
      href: '/app/chat',
      internal: true,
      newTab: false,
    });
    assert.deepStrictEqual(chipTarget(byId('users')), {
      href: '/admin/users',
      internal: true,
      newTab: false,
    });
    assert.deepStrictEqual(chipTarget(byId('guide')), {
      href: 'https://docs.example.edu',
      internal: false,
      newTab: true,
    });
  });
});

describe('frameUrl', () => {
  const chat = shellSections(assemble(CATALOG, { origin: ORIGIN, isAdmin: false }))[0].chips[0];

  it('opens a deep link on the chip origin', () => {
    assert.strictEqual(
      frameUrl(chat, '/c/123?x=1#m'),
      'https://chat.scout.example.edu/c/123?x=1#m',
    );
  });

  it('falls back to the chip destination for anything off its origin', () => {
    for (const path of [
      null,
      '',
      'c/123',
      '//evil.example/x',
      'https://evil.example/x',
      '/\\evil.example',
    ]) {
      assert.strictEqual(frameUrl(chat, path), chat.href, String(path));
    }
  });
});

describe('shellLocation', () => {
  const chat = shellSections(assemble(CATALOG, { origin: ORIGIN, isAdmin: false }))[0].chips[0];

  it('maps a location on the chip origin to its launchpad address', () => {
    assert.strictEqual(
      shellLocation(chat, 'https://chat.scout.example.edu/c/123?x=1#m'),
      '/app/chat/c/123?x=1#m',
    );
    assert.strictEqual(shellLocation(chat, 'https://chat.scout.example.edu/'), '/app/chat');
  });

  it('round-trips through deepPath and frameUrl', () => {
    for (const href of [
      'https://chat.scout.example.edu/c/123?x=1&y=a%20b#m',
      'https://chat.scout.example.edu/browser/lake/bronze/',
      'https://chat.scout.example.edu/?models=none',
    ]) {
      const address = new URL(shellLocation(chat, href)!, 'https://scout.example.edu');
      assert.strictEqual(frameUrl(chat, deepPath('chat', address)), href, href);
    }
  });

  it('takes the path from a subdomain of the chip host', () => {
    assert.strictEqual(
      shellLocation(chat, 'https://alice.chat.scout.example.edu/user/alice/lab'),
      '/app/chat/user/alice/lab',
    );
  });

  it('ignores anything that is not a location on the chip origin', () => {
    for (const href of [
      undefined,
      42,
      'not a url',
      'https://evil.example/c/123',
      'https://jupyter.scout.example.edu/lab',
      'http://chat.scout.example.edu/c/123',
      'https://chat.scout.example.edu.evil.example/c/123',
      'https://xchat.scout.example.edu/c/123',
      'https://chat.scout.example.edu:8443/c/123',
    ]) {
      assert.strictEqual(shellLocation(chat, href), null, String(href));
    }
  });
});

describe('deepPath', () => {
  const at = (href: string) => deepPath('chat', new URL(href, 'https://scout.example.edu'));

  it('reads the app page out of the launchpad address', () => {
    assert.strictEqual(at('/app/chat/c/123?x=1#m'), '/c/123?x=1#m');
    assert.strictEqual(at('/app/chat/'), '/');
    assert.strictEqual(at('/app/chat?models=none'), '/?models=none');
  });

  it('is null for the start page and for other apps', () => {
    for (const href of ['/app/chat', '/app/chatty/c/123', '/app/analytics/c/123', '/']) {
      assert.strictEqual(at(href), null, href);
    }
  });
});

describe('recentApps', () => {
  it('moves the app to the end and keeps only the most recent', () => {
    assert.deepStrictEqual(recentApps(['a', 'b', 'c'], 'd', 3), ['b', 'c', 'd']);
    assert.deepStrictEqual(recentApps(['a', 'b', 'c'], 'a', 3), ['b', 'c', 'a']);
    assert.deepStrictEqual(recentApps([], 'a', 3), ['a']);
  });
});

describe('isChipOrigin', () => {
  const chat = shellSections(assemble(CATALOG, { origin: ORIGIN, isAdmin: false }))[0].chips[0];

  it('accepts the chip origin and its subdomains only', () => {
    assert.strictEqual(isChipOrigin(chat, 'https://chat.scout.example.edu'), true);
    assert.strictEqual(isChipOrigin(chat, 'https://alice.chat.scout.example.edu'), true);
    for (const origin of [
      'null',
      '',
      'https://scout.example.edu',
      'https://xchat.scout.example.edu',
    ]) {
      assert.strictEqual(isChipOrigin(chat, origin), false, origin);
    }
  });
});

describe('chipForUrl', () => {
  const sections = shellSections(
    assemble(
      catalogFrom({
        chips: [
          {
            id: 'orchestrator',
            title: 'Orchestrator',
            link: { subdomain: 'temporal', path: '/auth/sso' },
          },
          {
            id: 'rads',
            title: 'RADS',
            link: { subdomain: 'playbooks', path: '/voila/render/rads.ipynb' },
            weight: 10,
          },
          {
            id: 'followup',
            title: 'Follow-up',
            link: { subdomain: 'playbooks', path: '/voila/render/followup.ipynb' },
            weight: 20,
          },
          { id: 'guide', title: 'Guide', link: { url: 'https://docs.example.edu' } },
        ],
      }),
      { origin: ORIGIN, isAdmin: false },
    ),
  );
  const find = (url: string) => chipForUrl(sections, new URL(url))?.id;

  it('picks the chip whose path prefixes the URL, else the first on the host', () => {
    assert.strictEqual(
      find('https://playbooks.scout.example.edu/voila/render/followup.ipynb?x=1'),
      'followup',
    );
    assert.strictEqual(find('https://playbooks.scout.example.edu/voila/tree'), 'rads');
    assert.strictEqual(
      find('https://temporal.scout.example.edu/namespaces/default/workflows'),
      'orchestrator',
    );
  });

  it('finds nothing off the catalog hosts', () => {
    assert.strictEqual(find('https://report-viewer.scout.example.edu/'), undefined);
    assert.strictEqual(find('https://docs.example.edu/'), undefined);
  });
});

describe('serviceHost', () => {
  it('accepts subdomains of the launchpad host, at any depth', () => {
    assert.strictEqual(
      serviceHost('Superset.scout.example.edu', 'scout.example.edu'),
      'superset.scout.example.edu',
    );
    assert.strictEqual(
      serviceHost('alice.jupyter.scout.example.edu', 'scout.example.edu'),
      'alice.jupyter.scout.example.edu',
    );
    assert.strictEqual(serviceHost('chat.localhost:3998', 'localhost:3998'), 'chat.localhost:3998');
  });

  it('rejects other hosts and malformed labels', () => {
    for (const value of [
      'scout.example.edu',
      'superset',
      'evil.example.com',
      'scout.example.edu.evil.example.com',
      'xscout.example.edu',
      '.scout.example.edu',
      'a..scout.example.edu',
      'a_b.scout.example.edu',
      'evil.com/.scout.example.edu',
    ]) {
      assert.strictEqual(serviceHost(value, 'scout.example.edu'), null, value);
    }
  });
});

describe('embeddedChip', () => {
  const sections = shellSections(assemble(CATALOG, { origin: ORIGIN, isAdmin: true }));

  it('finds only services, and nothing when embedding is off', () => {
    assert.strictEqual(embeddedChip(sections, 'chat')?.title, 'Chat');
    assert.strictEqual(embeddedChip(sections, 'users'), undefined);
    assert.strictEqual(embeddedChip(sections, 'guide'), undefined);
  });
});

describe('openRequest', () => {
  const sections = shellSections(assemble(CATALOG, { origin: ORIGIN, isAdmin: false }));
  const launchpad = new URL('https://scout.example.edu/app/chat');
  const open = (origin: string, href: unknown) => openRequest(sections, launchpad, origin, href);

  it('opens a Scout page from any Scout frame in the app that owns it', () => {
    const request = open(
      'https://report-viewer.scout.example.edu',
      'https://chat.scout.example.edu/c/1?x=1',
    );
    assert.strictEqual(request?.chip.id, 'chat');
    assert.strictEqual(request?.address, '/app/chat/c/1?x=1');
    assert.strictEqual(
      open('https://scout.example.edu', 'https://jupyter.scout.example.edu/')?.address,
      '/app/notebooks',
    );
  });

  it('ignores senders and pages off the launchpad host, and pages no chip owns', () => {
    const page = 'https://chat.scout.example.edu/c/1';
    for (const origin of [
      'null',
      '',
      'https://evil.example',
      'http://chat.scout.example.edu',
      'https://scout.example.edu.evil.example',
    ]) {
      assert.strictEqual(open(origin, page), null, origin);
    }
    for (const href of [
      undefined,
      42,
      'not a url',
      'https://evil.example/c/1',
      'https://datasets.scout.example.edu/x',
      'javascript:alert(1)',
    ]) {
      assert.strictEqual(open('https://chat.scout.example.edu', href), null, String(href));
    }
  });
});
