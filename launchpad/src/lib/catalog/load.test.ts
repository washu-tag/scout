import { promises as fs } from 'fs';
import os from 'os';
import path from 'path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { catalogDirs, loadCatalog, resetCatalogSnapshotForTests } from './load';
import { CATALOG_API_VERSION } from './schema';

const ENV_KEYS = ['LAUNCHPAD_CATALOG_DIRS', 'ENABLE_CHAT', 'ENABLE_PLAYBOOKS', 'ENABLE_MINIO'];
const savedEnv: Record<string, string | undefined> = {};

function chipYaml(id: string, title = id): string {
  return [
    `apiVersion: ${CATALOG_API_VERSION}`,
    'chips:',
    `  - id: ${id}`,
    `    title: ${title}`,
    `    link: { subdomain: ${id} }`,
    '',
  ].join('\n');
}

let tmpDir: string;

beforeEach(async () => {
  for (const key of ENV_KEYS) {
    savedEnv[key] = process.env[key];
    delete process.env[key];
  }
  resetCatalogSnapshotForTests();
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'launchpad-catalog-'));
});

afterEach(async () => {
  for (const key of ENV_KEYS) {
    if (savedEnv[key] === undefined) delete process.env[key];
    else process.env[key] = savedEnv[key];
  }
  await fs.rm(tmpDir, { recursive: true, force: true });
});

describe('catalogDirs', () => {
  it('splits and trims the colon-separated env var', () => {
    process.env.LAUNCHPAD_CATALOG_DIRS = ' /a : /b :';
    expect(catalogDirs()).toEqual(['/a', '/b']);
  });
});

describe('loadCatalog', () => {
  it('falls back to the builtin document when no directories are configured', async () => {
    const catalog = await loadCatalog();
    const ids = catalog.chips.map((chip) => chip.id);
    expect(ids).toContain('analytics');
    expect(ids).toContain('notebooks');
    // Env flags gate the builtin exactly as the deployment used to.
    expect(catalog.chips.find((chip) => chip.id === 'chat')?.enabled).toBe(false);
    expect(catalog.chips.find((chip) => chip.id === 'lake')?.enabled).toBe(true);
  });

  it('reads YAML documents from the configured directories', async () => {
    await fs.writeFile(path.join(tmpDir, 'good.yaml'), chipYaml('alpha'));
    await fs.writeFile(path.join(tmpDir, 'broken.yaml'), 'chips: [unclosed');
    await fs.writeFile(path.join(tmpDir, 'fetched.url'), 'https://example.com');
    process.env.LAUNCHPAD_CATALOG_DIRS = tmpDir;

    const catalog = await loadCatalog();
    expect(catalog.chips.map((chip) => chip.id)).toEqual(['alpha']);
    const messages = catalog.diagnostics.map((d) => d.message).join('\n');
    expect(messages).toContain('YAML parse error');
    expect(messages).toContain('not a .yaml/.yml key');
  });

  it('ignores directories that do not exist', async () => {
    await fs.writeFile(path.join(tmpDir, 'good.yaml'), chipYaml('alpha'));
    process.env.LAUNCHPAD_CATALOG_DIRS = `${path.join(tmpDir, 'absent')}:${tmpDir}`;
    const catalog = await loadCatalog();
    expect(catalog.chips.map((chip) => chip.id)).toEqual(['alpha']);
  });

  it('ranks earlier directories above later ones for group definitions', async () => {
    const mounted = path.join(tmpDir, 'mounted');
    const discovered = path.join(tmpDir, 'discovered');
    await fs.mkdir(mounted);
    await fs.mkdir(discovered);
    await fs.writeFile(
      path.join(mounted, 'core.yaml'),
      [`apiVersion: ${CATALOG_API_VERSION}`, 'groups:', '  - { id: g, title: Mounted }', ''].join(
        '\n',
      ),
    );
    await fs.writeFile(
      path.join(discovered, 'plugin.yaml'),
      [
        `apiVersion: ${CATALOG_API_VERSION}`,
        'groups:',
        '  - { id: g, title: Discovered, weight: 1 }',
        '',
      ].join('\n'),
    );
    process.env.LAUNCHPAD_CATALOG_DIRS = `${mounted}:${discovered}`;
    const catalog = await loadCatalog();
    const ranks = Object.fromEntries(catalog.groups.map((g) => [g.title, g.sourceRank]));
    expect(ranks).toEqual({ Mounted: 0, Discovered: 1 });
  });

  it('serves the cached snapshot within the TTL and rebuilds after a change', async () => {
    await fs.writeFile(path.join(tmpDir, 'apps.yaml'), chipYaml('alpha'));
    process.env.LAUNCHPAD_CATALOG_DIRS = tmpDir;

    const t0 = 1_000_000;
    const first = await loadCatalog(t0);
    expect(first.chips.map((chip) => chip.id)).toEqual(['alpha']);

    // Change on disk, ask again inside the TTL: still the cached snapshot.
    await fs.writeFile(path.join(tmpDir, 'apps.yaml'), chipYaml('beta', 'Beta Beta'));
    const withinTtl = await loadCatalog(t0 + 1_000);
    expect(withinTtl.chips.map((chip) => chip.id)).toEqual(['alpha']);

    // Past the TTL the signature differs (size changed), so it rebuilds.
    const afterTtl = await loadCatalog(t0 + 60_000);
    expect(afterTtl.chips.map((chip) => chip.id)).toEqual(['beta']);
  });
});
