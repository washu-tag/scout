import { describe, expect, it } from 'vitest';
import { assemble, resolveHref, type Origin } from './assemble';
import { builtinCatalog } from './builtin';
import { validateDocument } from './schema';
import { CATALOG_API_VERSION } from './schema';
import type { Catalog } from './types';

const ORIGIN: Origin = { protocol: 'https', host: 'scout.example.edu' };

const ALL_FLAGS = {
  enableChat: true,
  enablePlaybooks: true,
  enableMinio: true,
  docsUrl: 'https://docs.example.edu',
};

function catalogFrom(document: Record<string, unknown>, source = 'test/apps.yaml'): Catalog {
  return validateDocument({ apiVersion: CATALOG_API_VERSION, ...document }, source);
}

describe('resolveHref', () => {
  it('resolves the three destination shapes', () => {
    expect(resolveHref({ subdomain: 'superset' }, ORIGIN)).toBe(
      'https://superset.scout.example.edu',
    );
    expect(resolveHref({ subdomain: 'temporal', path: '/auth/sso' }, ORIGIN)).toBe(
      'https://temporal.scout.example.edu/auth/sso',
    );
    expect(resolveHref({ path: '/admin/users' }, ORIGIN)).toBe('/admin/users');
    expect(resolveHref({ url: 'https://docs.example.edu' }, ORIGIN)).toBe(
      'https://docs.example.edu',
    );
  });
});

describe('assemble on the builtin catalog', () => {
  it('reproduces the classic page for an admin with every flag on', () => {
    const model = assemble(builtinCatalog(ALL_FLAGS), { origin: ORIGIN, isAdmin: true });
    expect(model.rows.map((row) => row.groups.map((group) => group.id))).toEqual([
      ['core'],
      ['playbooks', 'admin'],
    ]);
    const core = model.rows[0].groups[0];
    expect(core.chips.map((chip) => chip.id)).toEqual(['chat', 'analytics', 'notebooks']);
    expect(core.columns).toBe(3);
    expect(core.footerLink?.url).toBe('https://docs.example.edu');
    const admin = model.rows[1].groups[1];
    expect(admin.layout).toBe('tiles');
    expect(admin.chips.map((chip) => chip.id)).toEqual([
      'admin-users',
      'lake',
      'orchestrator',
      'monitor',
    ]);
    expect(admin.chips[2].href).toBe('https://temporal.scout.example.edu/auth/sso');
  });

  it('renders playbooks full-width for a non-admin (unpaired half)', () => {
    const model = assemble(builtinCatalog(ALL_FLAGS), { origin: ORIGIN, isAdmin: false });
    expect(model.rows.map((row) => row.groups.map((group) => group.id))).toEqual([
      ['core'],
      ['playbooks'],
    ]);
    expect(model.diagnostics).toEqual([]);
  });

  it('drops the chat column when chat is disabled', () => {
    const model = assemble(builtinCatalog({ ...ALL_FLAGS, enableChat: false }), {
      origin: ORIGIN,
      isAdmin: false,
    });
    const core = model.rows[0].groups[0];
    expect(core.chips.map((chip) => chip.id)).toEqual(['analytics', 'notebooks']);
    expect(core.columns).toBe(2);
  });

  it('hides the playbooks section entirely when disabled', () => {
    const model = assemble(builtinCatalog({ ...ALL_FLAGS, enablePlaybooks: false }), {
      origin: ORIGIN,
      isAdmin: false,
    });
    expect(model.rows.map((row) => row.groups.map((group) => group.id))).toEqual([['core']]);
  });
});

describe('assemble mechanics', () => {
  it('synthesizes a group for chips that reference an undefined one', () => {
    const catalog = catalogFrom({
      chips: [{ id: 'xnat', title: 'XNAT', link: { subdomain: 'xnat' }, group: 'imaging' }],
    });
    const model = assemble(catalog, { origin: ORIGIN, isAdmin: true });
    expect(model.rows).toHaveLength(1);
    const group = model.rows[0].groups[0];
    expect(group.id).toBe('imaging');
    expect(group.title).toBe('Imaging');
    expect(group.layout).toBe('cards');
    expect(model.diagnostics.some((d) => d.message.includes('synthesized'))).toBe(true);
  });

  it('lets the lower source rank win a group id collision', () => {
    const mounted = validateDocument(
      {
        apiVersion: CATALOG_API_VERSION,
        groups: [{ id: 'imaging', title: 'Imaging (core)', weight: 15 }],
      },
      'catalog/core.yaml',
      0,
    );
    const discovered = validateDocument(
      {
        apiVersion: CATALOG_API_VERSION,
        groups: [{ id: 'imaging', title: 'Imaging (plugin)', weight: 5 }],
        chips: [{ id: 'xnat', title: 'XNAT', link: { subdomain: 'xnat' }, group: 'imaging' }],
      },
      'discovered/plugin.yaml',
      1,
    );
    const catalog: Catalog = {
      chips: [...mounted.chips, ...discovered.chips],
      groups: [...mounted.groups, ...discovered.groups],
      diagnostics: [...mounted.diagnostics, ...discovered.diagnostics],
    };
    const model = assemble(catalog, { origin: ORIGIN, isAdmin: true });
    expect(model.rows[0].groups[0].title).toBe('Imaging (core)');
    expect(model.diagnostics.some((d) => d.message.includes('already defined'))).toBe(true);
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
    expect(userModel.rows).toHaveLength(0);
    const adminModel = assemble(catalog, { origin: ORIGIN, isAdmin: true });
    expect(adminModel.rows[0].groups[0].chips.map((chip) => chip.id)).toEqual(['a']);
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
    expect(model.rows.map((row) => row.groups[0].id)).toEqual(['earlier', 'later']);
    expect(model.rows[0].groups[0].chips.map((chip) => chip.id)).toEqual(['first', 'a', 'z']);
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
    expect(model.rows[0].groups[0].columns).toBe(2);
  });

  it('withholds diagnostics from non-admins', () => {
    const catalog = catalogFrom({
      chips: [{ id: 'bad', title: '', link: { path: '/x' } }],
    });
    expect(assemble(catalog, { origin: ORIGIN, isAdmin: false }).diagnostics).toEqual([]);
    expect(assemble(catalog, { origin: ORIGIN, isAdmin: true }).diagnostics.length).toBeGreaterThan(
      0,
    );
  });
});
