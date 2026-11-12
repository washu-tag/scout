import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { assemble, type Origin } from './assemble';
import { CATALOG_API_VERSION, CATALOG_KIND, validateDocument } from './schema';
import { chipTarget, embeddedChip, shellSections } from './shell';
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
    assert.deepStrictEqual(chipTarget(byId('chat'), true), {
      href: '/app/chat',
      internal: true,
      newTab: false,
    });
    assert.deepStrictEqual(chipTarget(byId('users'), true), {
      href: '/admin/users',
      internal: true,
      newTab: false,
    });
    assert.deepStrictEqual(chipTarget(byId('guide'), true), {
      href: 'https://docs.example.edu',
      internal: false,
      newTab: true,
    });
  });

  it('opens services directly when embedding is off', () => {
    assert.deepStrictEqual(chipTarget(byId('chat'), false), {
      href: 'https://chat.scout.example.edu',
      internal: false,
      newTab: true,
    });
  });
});

describe('embeddedChip', () => {
  const sections = shellSections(assemble(CATALOG, { origin: ORIGIN, isAdmin: true }));

  it('finds only services, and nothing when embedding is off', () => {
    assert.strictEqual(embeddedChip(sections, 'chat', true)?.title, 'Chat');
    assert.strictEqual(embeddedChip(sections, 'users', true), undefined);
    assert.strictEqual(embeddedChip(sections, 'guide', true), undefined);
    assert.strictEqual(embeddedChip(sections, 'chat', false), undefined);
  });
});
