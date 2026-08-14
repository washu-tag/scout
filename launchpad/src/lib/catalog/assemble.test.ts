import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { assemble, resolveHref, type Origin } from './assemble';
import { CATALOG_API_VERSION, CATALOG_KIND, validateDocument } from './schema';
import type { Catalog } from './types';

const ORIGIN: Origin = { protocol: 'https', host: 'scout.example.edu' };

function catalogFrom(document: Record<string, unknown>, source = 'test/apps.yaml'): Catalog {
  return validateDocument(
    { apiVersion: CATALOG_API_VERSION, kind: CATALOG_KIND, ...document },
    source,
  );
}

function merged(...catalogs: Catalog[]): Catalog {
  return {
    chips: catalogs.flatMap((c) => c.chips),
    groups: catalogs.flatMap((c) => c.groups),
    diagnostics: catalogs.flatMap((c) => c.diagnostics),
  };
}

// The classic launchpad page as one catalog document — what the chart's core
// catalog plus the per-service documents add up to in a full deployment.
const CLASSIC = {
  groups: [
    {
      id: 'core',
      title: 'Core Services',
      icon: 'cube',
      weight: 10,
      layout: 'cards',
      footerLink: {
        text: 'New to Scout? Check out our documentation',
        url: 'https://docs.example.edu',
      },
    },
    {
      id: 'playbooks',
      title: 'Playbooks',
      icon: 'book-open',
      weight: 20,
      layout: 'rows',
      width: 'half',
    },
    {
      id: 'admin',
      title: 'Admin Tools',
      icon: 'cog',
      weight: 30,
      layout: 'tiles',
      width: 'half',
      audience: 'admin',
    },
  ],
  chips: [
    { id: 'chat', title: 'Chat', link: { subdomain: 'chat' }, group: 'core', weight: 10 },
    {
      id: 'analytics',
      title: 'Analytics',
      link: { subdomain: 'superset' },
      group: 'core',
      weight: 20,
    },
    {
      id: 'notebooks',
      title: 'Notebooks',
      link: { subdomain: 'jupyter' },
      group: 'core',
      weight: 30,
    },
    {
      id: 'playbook-cohort',
      title: 'Research Cohorting',
      link: { subdomain: 'playbooks', path: '/voila/render/cohort/Cohort.ipynb' },
      group: 'playbooks',
      weight: 10,
    },
    {
      id: 'admin-users',
      title: 'Users',
      link: { path: '/admin/users' },
      group: 'admin',
      weight: 10,
      audience: 'admin',
    },
    {
      id: 'lake',
      title: 'Lake',
      link: { subdomain: 'minio' },
      group: 'admin',
      weight: 20,
      audience: 'admin',
    },
    {
      id: 'orchestrator',
      title: 'Orchestrator',
      link: { subdomain: 'temporal', path: '/auth/sso' },
      group: 'admin',
      weight: 30,
      audience: 'admin',
    },
    {
      id: 'monitor',
      title: 'Monitor',
      link: { subdomain: 'grafana' },
      group: 'admin',
      weight: 40,
      audience: 'admin',
    },
  ],
};

describe('resolveHref', () => {
  it('resolves the three destination shapes', () => {
    assert.strictEqual(
      resolveHref({ subdomain: 'superset' }, ORIGIN),
      'https://superset.scout.example.edu',
    );
    assert.strictEqual(
      resolveHref({ subdomain: 'temporal', path: '/auth/sso' }, ORIGIN),
      'https://temporal.scout.example.edu/auth/sso',
    );
    assert.strictEqual(resolveHref({ path: '/admin/users' }, ORIGIN), '/admin/users');
    assert.strictEqual(
      resolveHref({ url: 'https://docs.example.edu' }, ORIGIN),
      'https://docs.example.edu',
    );
  });
});

describe('assemble on the classic page', () => {
  it('reproduces the classic layout for an admin', () => {
    const model = assemble(catalogFrom(CLASSIC), { origin: ORIGIN, isAdmin: true });
    assert.deepStrictEqual(
      model.rows.map((row) => row.groups.map((group) => group.id)),
      [['core'], ['playbooks', 'admin']],
    );
    const core = model.rows[0].groups[0];
    assert.deepStrictEqual(
      core.chips.map((chip) => chip.id),
      ['chat', 'analytics', 'notebooks'],
    );
    assert.strictEqual(core.columns, 3);
    assert.strictEqual(core.footerLink?.url, 'https://docs.example.edu');
    assert.strictEqual(core.chips[0].source, 'test/apps.yaml');
    const admin = model.rows[1].groups[1];
    assert.strictEqual(admin.layout, 'tiles');
    assert.deepStrictEqual(
      admin.chips.map((chip) => chip.id),
      ['admin-users', 'lake', 'orchestrator', 'monitor'],
    );
    assert.strictEqual(admin.chips[2].href, 'https://temporal.scout.example.edu/auth/sso');
  });

  it('renders playbooks full-width for a non-admin (unpaired half)', () => {
    const model = assemble(catalogFrom(CLASSIC), { origin: ORIGIN, isAdmin: false });
    assert.deepStrictEqual(
      model.rows.map((row) => row.groups.map((group) => group.id)),
      [['core'], ['playbooks']],
    );
    assert.deepStrictEqual(model.diagnostics, []);
  });
});

describe('assemble mechanics', () => {
  it('synthesizes a group for chips that reference an undefined one', () => {
    const catalog = catalogFrom({
      chips: [{ id: 'xnat', title: 'XNAT', link: { subdomain: 'xnat' }, group: 'imaging' }],
    });
    const model = assemble(catalog, { origin: ORIGIN, isAdmin: true });
    assert.strictEqual(model.rows.length, 1);
    const group = model.rows[0].groups[0];
    assert.strictEqual(group.id, 'imaging');
    assert.strictEqual(group.title, 'Imaging');
    assert.strictEqual(group.layout, 'cards');
    assert.ok(
      model.diagnostics.some((d) => d.message.includes('synthesized')),
      'expected a synthesized-group diagnostic',
    );
  });

  it('lets the lower source rank win a group id collision', () => {
    const mounted = validateDocument(
      {
        apiVersion: CATALOG_API_VERSION,
        kind: CATALOG_KIND,
        groups: [{ id: 'imaging', title: 'Imaging (core)', weight: 15 }],
      },
      'catalog/core.yaml',
      0,
    );
    const discovered = validateDocument(
      {
        apiVersion: CATALOG_API_VERSION,
        kind: CATALOG_KIND,
        groups: [{ id: 'imaging', title: 'Imaging (plugin)', weight: 5 }],
        chips: [{ id: 'xnat', title: 'XNAT', link: { subdomain: 'xnat' }, group: 'imaging' }],
      },
      'discovered/plugin.yaml',
      1,
    );
    const model = assemble(merged(mounted, discovered), { origin: ORIGIN, isAdmin: true });
    assert.strictEqual(model.rows[0].groups[0].title, 'Imaging (core)');
    assert.ok(
      model.diagnostics.some((d) => d.message.includes('already defined')),
      'expected an already-defined diagnostic',
    );
  });

  it('renders same-id chips from different sources side by side (ids are per-source)', () => {
    const mounted = validateDocument(
      {
        apiVersion: CATALOG_API_VERSION,
        kind: CATALOG_KIND,
        groups: [{ id: 'g', title: 'G' }],
        chips: [{ id: 'docs', title: 'Docs A', link: { path: '/a' }, group: 'g' }],
      },
      'catalog/core.yaml',
      0,
    );
    const discovered = validateDocument(
      {
        apiVersion: CATALOG_API_VERSION,
        kind: CATALOG_KIND,
        chips: [{ id: 'docs', title: 'Docs B', link: { path: '/b' }, group: 'g' }],
      },
      'discovered/other.yaml',
      1,
    );
    const model = assemble(merged(mounted, discovered), { origin: ORIGIN, isAdmin: false });
    const chips = model.rows[0].groups[0].chips;
    assert.deepStrictEqual(
      chips.map((chip) => `${chip.source}:${chip.id}`),
      ['catalog/core.yaml:docs', 'discovered/other.yaml:docs'],
    );
  });

  it('filters disabled chips, admin chips, and then empty groups', () => {
    const catalog = catalogFrom({
      groups: [{ id: 'ops', title: 'Ops', audience: 'admin' }],
      chips: [
        { id: 'a', title: 'A', link: { path: '/a' }, group: 'ops', audience: 'admin' },
        { id: 'b', title: 'B', link: { path: '/b' }, group: 'ops', enabled: false },
      ],
    });
    const userModel = assemble(catalog, { origin: ORIGIN, isAdmin: false });
    assert.strictEqual(userModel.rows.length, 0);
    const adminModel = assemble(catalog, { origin: ORIGIN, isAdmin: true });
    assert.deepStrictEqual(
      adminModel.rows[0].groups[0].chips.map((chip) => chip.id),
      ['a'],
    );
  });

  it('reports a chip whose audience is wider than its group', () => {
    const catalog = catalogFrom({
      groups: [{ id: 'ops', title: 'Ops', audience: 'admin' }],
      chips: [{ id: 'a', title: 'A', link: { path: '/a' }, group: 'ops' }],
    });
    // The admin sees the chip and the warning; the non-admin sees no section at
    // all, which is exactly why the warning must not depend on the viewer.
    const admin = assemble(catalog, { origin: ORIGIN, isAdmin: true });
    assert.ok(
      admin.diagnostics.some((d) => d.subject === 'a' && d.message.includes('wider than group')),
      'expected an audience-mismatch diagnostic',
    );
    assert.strictEqual(assemble(catalog, { origin: ORIGIN, isAdmin: false }).rows.length, 0);
  });

  it('orders groups and chips by weight, then title, then id', () => {
    const catalog = catalogFrom({
      groups: [
        { id: 'later', title: 'Later', weight: 20 },
        { id: 'earlier', title: 'Earlier', weight: 10 },
      ],
      chips: [
        { id: 'z', title: 'Same', link: { path: '/z' }, group: 'earlier', weight: 10 },
        { id: 'a', title: 'Same', link: { path: '/a' }, group: 'earlier', weight: 10 },
        { id: 'first', title: 'First', link: { path: '/f' }, group: 'earlier', weight: 5 },
        { id: 'only', title: 'Only', link: { path: '/o' }, group: 'later' },
      ],
    });
    const model = assemble(catalog, { origin: ORIGIN, isAdmin: false });
    assert.deepStrictEqual(
      model.rows.map((row) => row.groups[0].id),
      ['earlier', 'later'],
    );
    assert.deepStrictEqual(
      model.rows[0].groups[0].chips.map((chip) => chip.id),
      ['first', 'a', 'z'],
    );
  });

  it('caps columns at the visible chip count', () => {
    const catalog = catalogFrom({
      groups: [{ id: 'g', title: 'G', layout: 'cards', maxColumns: 4 }],
      chips: [
        { id: 'a', title: 'A', link: { path: '/a' }, group: 'g' },
        { id: 'b', title: 'B', link: { path: '/b' }, group: 'g' },
      ],
    });
    const model = assemble(catalog, { origin: ORIGIN, isAdmin: false });
    assert.strictEqual(model.rows[0].groups[0].columns, 2);
  });

  it('withholds diagnostics from non-admins', () => {
    const catalog = catalogFrom({
      chips: [{ id: 'bad', title: '', link: { path: '/x' } }],
    });
    assert.deepStrictEqual(assemble(catalog, { origin: ORIGIN, isAdmin: false }).diagnostics, []);
    assert.ok(
      assemble(catalog, { origin: ORIGIN, isAdmin: true }).diagnostics.length > 0,
      'an admin should see the skipped-chip diagnostic',
    );
  });
});
