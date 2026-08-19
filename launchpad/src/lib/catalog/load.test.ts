import assert from 'node:assert/strict';
import { promises as fs } from 'fs';
import os from 'os';
import path from 'path';
import { afterEach, beforeEach, describe, it, mock } from 'node:test';
import { catalogDirs, loadCatalog, resetCatalogSnapshotForTests } from './load';
import { CATALOG_API_VERSION, CATALOG_KIND } from './schema';

let savedDirs: string | undefined;

function chipYaml(id: string, title = id): string {
  return [
    `apiVersion: ${CATALOG_API_VERSION}`,
    `kind: ${CATALOG_KIND}`,
    'chips:',
    `  - id: ${id}`,
    `    title: ${title}`,
    `    link: { subdomain: ${id} }`,
    '',
  ].join('\n');
}

let tmpDir: string;

beforeEach(async () => {
  savedDirs = process.env.LAUNCHPAD_CATALOG_DIRS;
  delete process.env.LAUNCHPAD_CATALOG_DIRS;
  resetCatalogSnapshotForTests();
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'launchpad-catalog-'));
});

afterEach(async () => {
  if (savedDirs === undefined) delete process.env.LAUNCHPAD_CATALOG_DIRS;
  else process.env.LAUNCHPAD_CATALOG_DIRS = savedDirs;
  await fs.rm(tmpDir, { recursive: true, force: true });
});

describe('catalogDirs', () => {
  it('splits and trims the colon-separated env var', () => {
    process.env.LAUNCHPAD_CATALOG_DIRS = ' /a : /b :';
    assert.deepStrictEqual(catalogDirs(), ['/a', '/b']);
  });
});

describe('loadCatalog', () => {
  it('returns an empty catalog with a diagnostic when no directories are configured', async () => {
    const catalog = await loadCatalog();
    assert.deepStrictEqual(catalog.chips, []);
    assert.deepStrictEqual(catalog.groups, []);
    assert.strictEqual(catalog.diagnostics.length, 1);
    assert.match(catalog.diagnostics[0].message, /LAUNCHPAD_CATALOG_DIRS/);
  });

  it('reads YAML documents from the configured directories', async () => {
    await fs.writeFile(path.join(tmpDir, 'good.yaml'), chipYaml('alpha'));
    await fs.writeFile(path.join(tmpDir, 'broken.yaml'), 'chips: [unclosed');
    await fs.writeFile(path.join(tmpDir, 'fetched.url'), 'https://example.com');
    process.env.LAUNCHPAD_CATALOG_DIRS = tmpDir;

    const catalog = await loadCatalog();
    assert.deepStrictEqual(
      catalog.chips.map((chip) => chip.id),
      ['alpha'],
    );
    const messages = catalog.diagnostics.map((d) => d.message).join('\n');
    assert.match(messages, /YAML parse error/);
    assert.match(messages, /not a \.yaml\/\.yml key/);
  });

  it('loads what it can and reports an unreadable directory', async () => {
    await fs.writeFile(path.join(tmpDir, 'good.yaml'), chipYaml('alpha'));
    process.env.LAUNCHPAD_CATALOG_DIRS = `${path.join(tmpDir, 'absent')}:${tmpDir}`;
    const catalog = await loadCatalog();
    assert.deepStrictEqual(
      catalog.chips.map((chip) => chip.id),
      ['alpha'],
    );
    assert.ok(
      catalog.diagnostics.some((d) => d.message.includes('not readable')),
      'expected a not-readable diagnostic',
    );
  });

  it('ranks earlier directories above later ones for group definitions', async () => {
    const mounted = path.join(tmpDir, 'mounted');
    const discovered = path.join(tmpDir, 'discovered');
    await fs.mkdir(mounted);
    await fs.mkdir(discovered);
    await fs.writeFile(
      path.join(mounted, 'core.yaml'),
      [
        `apiVersion: ${CATALOG_API_VERSION}`,
        `kind: ${CATALOG_KIND}`,
        'groups:',
        '  - { id: g, title: Mounted }',
        '',
      ].join('\n'),
    );
    await fs.writeFile(
      path.join(discovered, 'plugin.yaml'),
      [
        `apiVersion: ${CATALOG_API_VERSION}`,
        `kind: ${CATALOG_KIND}`,
        'groups:',
        '  - { id: g, title: Discovered, weight: 1 }',
        '',
      ].join('\n'),
    );
    process.env.LAUNCHPAD_CATALOG_DIRS = `${mounted}:${discovered}`;
    const catalog = await loadCatalog();
    const ranks = Object.fromEntries(catalog.groups.map((g) => [g.title, g.sourceRank]));
    assert.deepStrictEqual(ranks, { Mounted: 0, Discovered: 1 });
  });

  it('serves the cached snapshot within the TTL and rebuilds after a change', async () => {
    await fs.writeFile(path.join(tmpDir, 'apps.yaml'), chipYaml('alpha'));
    process.env.LAUNCHPAD_CATALOG_DIRS = tmpDir;

    const t0 = 1_000_000;
    const first = await loadCatalog(t0);
    assert.deepStrictEqual(
      first.chips.map((chip) => chip.id),
      ['alpha'],
    );

    // Change on disk, ask again inside the TTL: still the cached snapshot.
    await fs.writeFile(path.join(tmpDir, 'apps.yaml'), chipYaml('beta', 'Beta Beta'));
    const withinTtl = await loadCatalog(t0 + 1_000);
    assert.deepStrictEqual(
      withinTtl.chips.map((chip) => chip.id),
      ['alpha'],
    );

    // Past the TTL the signature differs (size changed), so it rebuilds.
    const afterTtl = await loadCatalog(t0 + 60_000);
    assert.deepStrictEqual(
      afterTtl.chips.map((chip) => chip.id),
      ['beta'],
    );
  });

  it('shares one scan among concurrent requests (single-flight)', async () => {
    await fs.writeFile(path.join(tmpDir, 'apps.yaml'), chipYaml('alpha'));
    process.env.LAUNCHPAD_CATALOG_DIRS = tmpDir;
    const readdir = mock.method(fs, 'readdir');
    try {
      const [a, b, c] = await Promise.all([loadCatalog(), loadCatalog(), loadCatalog()]);
      assert.strictEqual(a, b);
      assert.strictEqual(b, c);
      assert.strictEqual(readdir.mock.callCount(), 1);
    } finally {
      readdir.mock.restore();
    }
  });
});
